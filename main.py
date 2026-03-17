import json
import sys
import time
import asyncio
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL
import db
import vector_store
from models import MemCell, AtomicFact, Foresight
from memory_layer.memcell_extractor import extract_segments
from memory_layer.episode_extractor import extract_episode
from memory_layer.cluster_manager import assign_to_scene
from memory_layer.profile_manager import detect_conflicts, detect_conflicts_batch
from memory_layer.profile_extractor import update_user_profile
from agentic_layer.vectorize_service import embed_text, embed_texts
from agentic_layer.memory_manager import agentic_retrieve

EXTRACTION_BATCH_SIZE = 10
BATCH_SLEEP = 0
STORAGE_CONCURRENCY = 10

_executor = ThreadPoolExecutor(max_workers=STORAGE_CONCURRENCY * 2)


def _extract_segment(seg: dict, current_date: str) -> dict:
    """Extract episode, facts, foresight, and scene hint for one segment (single LLM call)."""
    result = extract_episode(seg["dialogue"], current_date)
    return {
        "segment": seg,
        "episode_text": result["episode"],
        "atomic_facts": result["atomic_facts"],
        "foresight": result.get("foresight", []),
        "scene_hint": result.get("scene_hint"),
    }


async def _store_segment_data(ext: dict, source_id: str, episode_embedding: list[float],
                              semaphore: asyncio.Semaphore,
                              loop: asyncio.AbstractEventLoop,
                              current_date: str = None) -> dict:
    """Phase 1: Store memcell, facts, vectors, foresight, scene (NO conflict detection).

    Runs concurrently across segments, bounded by semaphore.
    """
    async with semaphore:
        seg = ext["segment"]
        episode_text = ext["episode_text"]
        atomic_facts = ext["atomic_facts"]
        foresight_signals = ext["foresight"]
        scene_hint = ext.get("scene_hint")

        seg_label = f"Segment {seg['segment_id']}: {seg['topic_hint']}"
        print(f"       [phase1] Starting {seg_label}")

        # Store MemCell
        cell = MemCell(episode_text=episode_text, raw_dialogue=seg["dialogue"],
                       source_id=source_id, conversation_date=current_date)
        memcell_id = await loop.run_in_executor(_executor, db.insert_memcell, cell)

        # Batch-embed all facts
        if atomic_facts:
            embeddings = await loop.run_in_executor(_executor, embed_texts, atomic_facts)
        else:
            embeddings = []

        # Insert facts into PostgreSQL
        fact_ids = []
        for fact_text in atomic_facts:
            fact = AtomicFact(memcell_id=memcell_id, fact_text=fact_text, conversation_date=current_date)
            fact_id = await loop.run_in_executor(_executor, db.insert_atomic_fact, fact)
            fact_ids.append(fact_id)

        # Upsert vectors into Qdrant
        for fact_id, embedding in zip(fact_ids, embeddings):
            await loop.run_in_executor(
                _executor, vector_store.upsert_fact, fact_id, memcell_id, embedding, current_date
            )

        # Store episode embedding in memcells table
        await loop.run_in_executor(_executor, db.update_memcell_embedding, memcell_id, episode_embedding)

        # Batch-embed foresight descriptions
        foresight_texts = [fs["description"] for fs in foresight_signals]
        foresight_embeddings = (
            await loop.run_in_executor(_executor, embed_texts, foresight_texts)
            if foresight_texts else []
        )

        # Store foresight signals with embeddings
        for i, fs in enumerate(foresight_signals):
            valid_from = None
            valid_until = None
            def _parse_foresight_dt(val):
                """Parse foresight datetime from LLM output (IST) and convert to UTC for storage."""
                if not val or val == "null" or val == "None":
                    return None
                IST = timezone(timedelta(hours=5, minutes=30))
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(str(val).strip(), fmt)
                        # LLM outputs IST (dialogue timestamps are IST) — convert to UTC for DB
                        dt_ist = dt.replace(tzinfo=IST)
                        return dt_ist.astimezone(timezone.utc).replace(tzinfo=None)
                    except ValueError:
                        continue
                return None

            try:
                valid_from = _parse_foresight_dt(fs.get("valid_from"))
                valid_until = _parse_foresight_dt(fs.get("valid_until"))
            except (ValueError, TypeError) as e:
                print(f"       Warning: Could not parse foresight dates: {e}")

            f = Foresight(
                memcell_id=memcell_id, description=fs["description"],
                valid_from=valid_from, valid_until=valid_until,
            )
            foresight_id = await loop.run_in_executor(_executor, db.insert_foresight, f)
            if i < len(foresight_embeddings):
                await loop.run_in_executor(_executor, db.update_foresight_embedding,
                                           foresight_id, foresight_embeddings[i])

        # Assign to MemScene (uses pre-computed embedding)
        scene_id = await loop.run_in_executor(
            _executor, assign_to_scene, memcell_id, episode_text, episode_embedding, scene_hint
        )

        print(f"   [phase1] Done {seg_label} → scene {scene_id}, {len(atomic_facts)} facts")

        return {
            "memcell_id": memcell_id,
            "scene_id": scene_id,
            "seg_label": seg_label,
            "fact_ids": fact_ids,
            "atomic_facts": atomic_facts,
            "embeddings": embeddings,
        }


async def _store_batch_async(batch_extractions: list[dict], episode_embeddings: list[list[float]],
                              source_id: str, interactive: bool,
                              current_date: str = None) -> list[dict]:
    """Two-phase storage: concurrent data insertion, then sequential conflict detection."""
    loop = asyncio.get_event_loop()
    semaphore = asyncio.Semaphore(STORAGE_CONCURRENCY)

    # Phase 1 — Concurrent: store all segment data in parallel
    phase1_start = time.time()
    phase1_tasks = [
        _store_segment_data(ext, source_id, emb, semaphore, loop, current_date=current_date)
        for ext, emb in zip(batch_extractions, episode_embeddings)
    ]
    phase1_results = await asyncio.gather(*phase1_tasks)
    print(f"\n       [Phase 1] Complete: {time.time() - phase1_start:.1f}s")

    # Phase 2 — Sequential per segment, batched within segment (1 LLM call per segment)
    phase2_start = time.time()
    print(f"       [Phase 2] Running conflict detection (segment-level batching)...")
    total_conflicts = 0
    conflict_counts = []
    for p1 in phase1_results:
        facts_with_embeddings = [
            {"fact_id": fid, "fact_text": ft, "embedding": emb}
            for fid, ft, emb in zip(p1["fact_ids"], p1["atomic_facts"], p1["embeddings"])
        ]
        seg_conflicts = await loop.run_in_executor(
            _executor, detect_conflicts_batch, facts_with_embeddings, interactive, current_date
        )
        if seg_conflicts:
            print(f"       [phase2] {p1['seg_label']}: {seg_conflicts} conflicts detected")
        conflict_counts.append(seg_conflicts)
        total_conflicts += seg_conflicts
    print(f"       [Phase 2] Complete: {total_conflicts} conflicts in {time.time() - phase2_start:.1f}s")

    total_storage = time.time() - phase1_start
    print(f"       Total storage: {total_storage:.1f}s")

    # Build final results
    results = []
    for p1, seg_conflicts in zip(phase1_results, conflict_counts):
        results.append({
            "memcell_id": p1["memcell_id"],
            "scene_id": p1["scene_id"],
            "facts": len(p1["atomic_facts"]),
            "conflicts": seg_conflicts,
        })

    return results


def ingest_conversation(conversation: list[dict], source_id: str = "default",
                        current_date: str = None, interactive: bool = False):
    """Async ingestion pipeline: parallel extraction, batch embedding, two-phase storage."""
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d")

    print(f"[1/2] Segmenting conversation ({len(conversation)} turns)...")
    segments = extract_segments(conversation)
    print(f"       Found {len(segments)} segments.")

    total_batches = (len(segments) + EXTRACTION_BATCH_SIZE - 1) // EXTRACTION_BATCH_SIZE
    print(f"\n[2/2] Processing {len(segments)} segments in {total_batches} batches (batch_size={EXTRACTION_BATCH_SIZE})...")
    pipeline_start = time.time()
    all_results = []

    for batch_start in range(0, len(segments), EXTRACTION_BATCH_SIZE):
        batch_end = min(batch_start + EXTRACTION_BATCH_SIZE, len(segments))
        batch = segments[batch_start:batch_end]
        batch_num = (batch_start // EXTRACTION_BATCH_SIZE) + 1

        if batch_start > 0 and BATCH_SLEEP > 0:
            print(f"\n       Sleeping {BATCH_SLEEP}s between batches...")
            time.sleep(BATCH_SLEEP)

        # Parallel extraction (one LLM call per segment, all in parallel)
        print(f"\n  ── Batch {batch_num}/{total_batches} ──")
        print(f"       Extracting {len(batch)} segments in parallel...")
        extract_start = time.time()
        batch_extractions = [None] * len(batch)

        with ThreadPoolExecutor(max_workers=EXTRACTION_BATCH_SIZE) as executor:
            future_to_idx = {
                executor.submit(_extract_segment, seg, current_date): i
                for i, seg in enumerate(batch)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                batch_extractions[idx] = future.result()
                seg_info = batch_extractions[idx]
                print(f"         Segment {batch_start+idx+1}: {seg_info['segment']['topic_hint']} "
                      f"({len(seg_info['atomic_facts'])} facts, {len(seg_info['foresight'])} foresight)")

        print(f"       Extraction: {time.time() - extract_start:.1f}s")

        # Batch-embed all episode texts in a single API call
        episode_texts = [ext["episode_text"] for ext in batch_extractions]
        episode_embeddings = embed_texts(episode_texts)

        # Two-phase storage: concurrent data insertion, sequential conflict detection
        store_start = time.time()

        batch_results = asyncio.run(
            _store_batch_async(batch_extractions, episode_embeddings, source_id, interactive,
                               current_date=current_date)
        )
        all_results.extend(batch_results)
        print(f"       Batch total: {time.time() - extract_start:.1f}s")

    print(f"\n       Pipeline complete in {time.time() - pipeline_start:.1f}s")

    # Update user profile from all active facts and scene summaries
    print(f"\n[Profile] Updating user profile...")
    update_user_profile()

    total_memcells = len(all_results)
    total_conflicts = sum(r["conflicts"] for r in all_results)
    print(f"\nDone. Ingested {total_memcells} MemCells, {total_conflicts} conflicts detected.")

    return [r["memcell_id"] for r in all_results]


def reset_databases():
    """Wipe and recreate all tables and Qdrant collections."""
    print("Resetting databases...")
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS conflicts CASCADE;
        DROP TABLE IF EXISTS foresight CASCADE;
        DROP TABLE IF EXISTS atomic_facts CASCADE;
        DROP TABLE IF EXISTS memcells CASCADE;
        DROP TABLE IF EXISTS memscenes CASCADE;
        DROP TABLE IF EXISTS user_profile CASCADE;
    """)
    conn.commit()
    cur.close()
    conn.close()

    from qdrant_client import QdrantClient
    from config import QDRANT_HOST, QDRANT_PORT
    qclient = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False, timeout=30)
    for name in ("facts", "scenes"):
        if qclient.collection_exists(name):
            qclient.delete_collection(name)

    db.init_schema()
    vector_store.init_collections()
    print("Databases reset.\n")


def main():
    if "--reset" in sys.argv:
        reset_databases()
    else:
        db.init_schema()
        vector_store.init_collections()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python main.py ingest <conversation.json> [--reset]")
        print("  python main.py query \"your question here\" [--verbose]")
        return

    command = sys.argv[1]

    if command == "ingest":
        if len(sys.argv) < 3:
            print("Usage: python main.py ingest <conversation.json> [--reset]")
            return
        filepath = sys.argv[2]
        with open(filepath) as f:
            data = json.load(f)
        conversation = data.get("conversation", data)
        source_id = data.get("source_id", filepath)
        current_date = data.get("date")
        interactive = "--interactive" in sys.argv or "-i" in sys.argv
        ingest_conversation(conversation, source_id, current_date=current_date, interactive=interactive)

    elif command == "query":
        if len(sys.argv) < 3:
            print("Usage: python main.py query \"your question here\" [--date YYYY-MM-DD] [--verbose]")
            return
        query = sys.argv[2]

        verbose = "--verbose" in sys.argv or "-v" in sys.argv

        # Parse --date for temporal query filtering
        query_time = None
        for i, arg in enumerate(sys.argv):
            if arg == "--date" and i + 1 < len(sys.argv):
                query_time = datetime.strptime(sys.argv[i + 1], "%Y-%m-%d")
                break

        result = agentic_retrieve(query, query_time=query_time, verbose=verbose)

        if verbose:
            print("\n" + "=" * 60)
            print("RETRIEVED CONTEXT:")
            print("=" * 60)
            print(result["context"])

        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""You are a helpful AI companion with memory of past conversations.
                    Answer the user's question using ONLY the memory context provided. Be concise and direct.
                    If the context doesn't contain relevant information, say so.

                    IMPORTANT: All sources include dates. When information conflicts, trust the
                    MOST RECENT source — more recent episodes and facts override older ones.
                    Only report what is true at the query time, not past states.
                    When asked about a future event, do not pick past facts from the context and vice-versa.
                    Do NOT cite source dates in your answer — just state what is true.

                    === QUERY DATE ===
                    {query_time.strftime('%Y-%m-%d') if query_time else 'now'}

                    === MEMORY CONTEXT  ===
                    {result['context']}

                    === QUESTION ===
                    {query}

                    Answer:"""
        
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

        print("\n" + "=" * 60)
        print("ANSWER:")
        print("=" * 60)
        print(response.text.strip())
        print(f"\n[Retrieval: {result['rounds']} round(s), sufficient: {result['is_sufficient']}]")

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
