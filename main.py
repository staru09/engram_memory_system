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
from memory_layer.profile_manager import detect_conflicts_batch
from memory_layer.profile_extractor import update_user_profile
from agentic_layer.vectorize_service import embed_text, embed_texts
from agentic_layer.memory_manager import agentic_retrieve

EXTRACTION_BATCH_SIZE = 10
BATCH_SLEEP = 0
STORAGE_CONCURRENCY = 10

_executor = ThreadPoolExecutor(max_workers=STORAGE_CONCURRENCY * 2)

client = genai.Client(api_key=GEMINI_API_KEY)


def _extract_segment(seg: dict, current_date: str, conversation_summary: str = None) -> dict:
    """Extract episode, facts, foresight, and scene hint for one segment (single LLM call)."""
    result = extract_episode(seg["dialogue"], current_date, conversation_summary=conversation_summary)
    return {
        "segment": seg,
        "episode_text": result["episode"],
        "atomic_facts": result["atomic_facts"],
        "foresight": result.get("foresight", []),
    }


def _rebuild_conversation_summary(new_episodes: list[str], current_date: str):
    """Rebuild the rolling conversation summary after ingesting new episodes."""
    existing_summary = db.get_conversation_summary()

    if not new_episodes:
        return

    episodes_text = "\n".join(f"- {ep}" for ep in new_episodes)

    if existing_summary:
        prompt = f"""You are maintaining a rolling summary of a long-term conversation between two people.

EXISTING SUMMARY:
{existing_summary}

NEW EPISODES (from session dated {current_date}):
{episodes_text}

Update the summary to incorporate the new episodes. Keep it concise (10-15 sentences max).
Focus on: key facts about each person, their relationship, ongoing plans, recent events, and any changes.
Drop details that are no longer relevant (cancelled plans, resolved issues).

Return ONLY the updated summary text, no JSON or formatting."""
    else:
        prompt = f"""You are creating a summary of a conversation between two people.

EPISODES (from session dated {current_date}):
{episodes_text}

Write a concise summary (10-15 sentences max).
Focus on: key facts about each person, their relationship, plans, events, and preferences.

Return ONLY the summary text, no JSON or formatting."""

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        summary = response.text.strip()
        db.upsert_conversation_summary(summary)
        print(f"  [summary] Updated ({len(summary)} chars)")
    except Exception as e:
        print(f"  [summary] Update failed: {e}")


def _consolidate_facts(facts: list[dict]) -> list[dict]:
    """Consolidate related facts within a session into denser entries (I3).
    Merges fragments like 'likes coffee' + 'prefers oat milk' → single entry."""
    if len(facts) <= 3:
        return facts  # too few to consolidate

    facts_text = "\n".join(f"- {f['text'] if isinstance(f, dict) else f}" for f in facts)

    prompt = f"""Given these extracted facts from one conversation session, merge closely related
facts into unified entries. Keep facts that cover distinct topics separate.

FACTS:
{facts_text}

Rules:
- Merge facts about the SAME topic/entity into a single comprehensive fact
  Example: "Alice likes coffee" + "Alice prefers oat milk" + "Alice likes it hot"
  → "Alice prefers hot coffee with oat milk."
- Keep facts about DIFFERENT topics separate
  Example: "Alice likes coffee" + "Alice went hiking" → keep as 2 separate facts
- Never lose specific details (dates, names, numbers, places) during merging
- Each merged fact must still be a self-contained assertion
- Category MUST be one of: personal_info, preferences, relationships, activities, goals, experiences, knowledge, opinions, habits, work_life

Output as JSON array:
[{{"text": "merged fact", "category": "category_name"}}]

Return ONLY the JSON array."""

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        consolidated = json.loads(text)
        if isinstance(consolidated, list) and len(consolidated) > 0:
            print(f"  [consolidate] {len(facts)} facts -> {len(consolidated)} consolidated")
            return consolidated
    except Exception as e:
        print(f"  [consolidate] Failed ({e}), using original facts")

    return facts


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

        seg_label = f"Segment {seg['segment_id']}: {seg['topic_hint']}"
        print(f"       [phase1] Starting {seg_label}")

        # Store MemCell
        cell = MemCell(episode_text=episode_text, raw_dialogue=seg["dialogue"],
                       source_id=source_id, conversation_date=current_date)
        memcell_id = await loop.run_in_executor(_executor, db.insert_memcell, cell)

        # Parse fact items (support both string and dict format)
        parsed_facts = []
        for fact_item in atomic_facts:
            if isinstance(fact_item, dict):
                parsed_facts.append({
                    "text": fact_item.get("text", ""),
                    "category": fact_item.get("category", "general"),
                })
            else:
                parsed_facts.append({"text": fact_item, "category": "general"})

        fact_texts = [f["text"] for f in parsed_facts]

        # Batch-embed all facts
        if fact_texts:
            embeddings = await loop.run_in_executor(_executor, embed_texts, fact_texts)
        else:
            embeddings = []

        # Insert facts into PostgreSQL
        fact_ids = []
        for pf in parsed_facts:
            fact = AtomicFact(memcell_id=memcell_id, fact_text=pf["text"],
                              category_name=pf["category"], conversation_date=current_date)
            fact_id = await loop.run_in_executor(_executor, db.insert_atomic_fact, fact)
            fact_ids.append(fact_id)

        # Upsert vectors into Qdrant
        for fact_id, pf, embedding in zip(fact_ids, parsed_facts, embeddings):
            await loop.run_in_executor(
                _executor, vector_store.upsert_fact, fact_id, memcell_id, embedding,
                current_date, pf["category"]
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
                """Parse foresight datetime from LLM output (IST). Stored as-is."""
                if not val or val == "null" or val == "None":
                    return None
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(str(val).strip(), fmt)
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

        print(f"   [phase1] Done {seg_label} → {len(atomic_facts)} facts")

        return {
            "memcell_id": memcell_id,
            "seg_label": seg_label,
            "fact_ids": fact_ids,
            "atomic_facts": atomic_facts,
            "parsed_facts": parsed_facts,
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

    # Phase 2 — Single conflict detection call for all facts across all segments
    all_batch_fact_ids = set()
    all_facts_with_embeddings = []
    for p1 in phase1_results:
        all_batch_fact_ids.update(p1.get("fact_ids", []))
        fact_texts = [pf["text"] for pf in p1["parsed_facts"]]
        for fid, ft, emb in zip(p1["fact_ids"], fact_texts, p1["embeddings"]):
            all_facts_with_embeddings.append({"fact_id": fid, "fact_text": ft, "embedding": emb})

    # Skip if too few pre-existing facts (nothing to conflict with)
    stats = db.get_system_stats()
    pre_existing = stats.get("active_facts", 0) - len(all_batch_fact_ids)

    phase2_start = time.time()
    if pre_existing < 10:
        print(f"       [Phase 2] Skipped conflict detection ({pre_existing} pre-existing facts)")
        total_conflicts = 0
    else:
        print(f"       [Phase 2] Running conflict detection ({len(all_facts_with_embeddings)} facts, {pre_existing} pre-existing)...")
        total_conflicts = await loop.run_in_executor(
            _executor, detect_conflicts_batch, all_facts_with_embeddings, interactive,
            current_date, all_batch_fact_ids
        )
    print(f"       [Phase 2] Complete: {total_conflicts} conflicts in {time.time() - phase2_start:.1f}s")

    total_storage = time.time() - phase1_start
    print(f"       Total storage: {total_storage:.1f}s")

    # Build final results
    results = []
    for i, p1 in enumerate(phase1_results):
        results.append({
            "memcell_id": p1["memcell_id"],
            "facts": len(p1["atomic_facts"]),
            "conflicts": total_conflicts if i == 0 else 0,
            "parsed_facts": p1.get("parsed_facts", []),
        })

    return results


def ingest_conversation(conversation: list[dict], source_id: str = "default",
                        current_date: str = None, interactive: bool = False,
                        extract_all_speakers: bool = False):
    """Async ingestion pipeline: parallel extraction, batch embedding, two-phase storage."""
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    print(f"[segment] Segmenting conversation ({len(conversation)} turns)...")
    segments = extract_segments(conversation, extract_all_speakers=extract_all_speakers)
    print(f"  Found {len(segments)} segments.")

    # Fetch conversation summary once — shared by all segments in this session
    conv_summary = db.get_conversation_summary()
    if conv_summary:
        print(f"[context] Using conversation summary ({len(conv_summary)} chars)")

    total_batches = (len(segments) + EXTRACTION_BATCH_SIZE - 1) // EXTRACTION_BATCH_SIZE
    pipeline_start = time.time()
    all_results = []
    all_new_episodes = []

    for batch_start in range(0, len(segments), EXTRACTION_BATCH_SIZE):
        batch_end = min(batch_start + EXTRACTION_BATCH_SIZE, len(segments))
        batch = segments[batch_start:batch_end]
        batch_num = (batch_start // EXTRACTION_BATCH_SIZE) + 1

        if batch_start > 0 and BATCH_SLEEP > 0:
            print(f"\n       Sleeping {BATCH_SLEEP}s between batches...")
            time.sleep(BATCH_SLEEP)

        # Parallel extraction with conversation summary context
        print(f"\n  -- Batch {batch_num}/{total_batches} --")
        print(f"  [extract] {len(batch)} segments in parallel...")
        extract_start = time.time()
        batch_extractions = [None] * len(batch)

        with ThreadPoolExecutor(max_workers=EXTRACTION_BATCH_SIZE) as executor:
            future_to_idx = {
                executor.submit(_extract_segment, seg, current_date, conv_summary): i
                for i, seg in enumerate(batch)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                batch_extractions[idx] = future.result()
                seg_info = batch_extractions[idx]
                print(f"    {seg_info['segment']['topic_hint']} "
                      f"({len(seg_info['atomic_facts'])} facts, {len(seg_info['foresight'])} foresight)")

        # Collect episode texts for summary rebuild
        for ext in batch_extractions:
            all_new_episodes.append(ext["episode_text"])


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

    print(f"\n  Pipeline complete in {time.time() - pipeline_start:.1f}s")

    # Post-pipeline: summary, profile, categories — all independent, run in parallel
    from memory_layer.profile_extractor import update_category_profiles
    from agentic_layer.fetch_mem_service import invalidate_category_cache

    new_facts_by_category = {}
    for r in all_results:
        for pf in r.get("parsed_facts", []):
            cat = pf.get("category", "general")
            if cat not in new_facts_by_category:
                new_facts_by_category[cat] = []
            new_facts_by_category[cat].append(pf["text"])

    post_start = time.time()
    print(f"\n[post-pipeline] Running summary + session_summary + profile + categories in parallel...")

    def _run_summary():
        t = time.time()
        _rebuild_conversation_summary(all_new_episodes, current_date)
        return time.time() - t

    def _run_session_summary():
        """Store per-session summary with embedding for retrieval use."""
        t = time.time()
        if not all_new_episodes:
            return 0.0
        episodes_text = "\n".join(f"- {ep}" for ep in all_new_episodes)
        prompt = f"""Summarize this conversation session in 3-5 sentences. Focus on key events, decisions, and new information shared.

EPISODES (from session dated {current_date}):
{episodes_text}

Return ONLY the summary text, no JSON or formatting."""
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            summary = response.text.strip()
            summary_embedding = embed_text(summary)
            db.insert_session_summary(source_id, current_date, summary, summary_embedding)
            print(f"  [session-summary] Stored ({len(summary)} chars)")
        except Exception as e:
            print(f"  [session-summary] Failed: {e}")
        return time.time() - t

    def _run_profile():
        t = time.time()
        all_new_facts = [pf["text"] for r in all_results for pf in r.get("parsed_facts", [])]
        update_user_profile(new_facts=all_new_facts if all_new_facts else None)
        return time.time() - t

    def _run_categories():
        t = time.time()
        if new_facts_by_category:
            update_category_profiles(new_facts_by_category)
            invalidate_category_cache()
        return time.time() - t

    with ThreadPoolExecutor(max_workers=4) as post_executor:
        summary_future = post_executor.submit(_run_summary)
        session_summary_future = post_executor.submit(_run_session_summary)
        profile_future = post_executor.submit(_run_profile)
        category_future = post_executor.submit(_run_categories)

        summary_time = summary_future.result()
        session_summary_time = session_summary_future.result()
        profile_time = profile_future.result()
        category_time = category_future.result()

    post_total = time.time() - post_start
    print(f"  [post-pipeline] Summary: {summary_time:.1f}s | Session: {session_summary_time:.1f}s | Profile: {profile_time:.1f}s | Categories: {category_time:.1f}s | Total: {post_total:.1f}s (parallel)")

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
        DROP TABLE IF EXISTS session_summaries CASCADE;
        DROP TABLE IF EXISTS profile_categories CASCADE;
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
