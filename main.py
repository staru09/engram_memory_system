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
from models import ConsolidatedFact
from memory_layer.memcell_extractor import extract_segments
from memory_layer.episode_extractor import extract_segment
from memory_layer.consolidator import consolidate_facts
from memory_layer.storage import store_batch
from memory_layer.profile_extractor import (
    update_user_profile, rebuild_conversation_summary, build_session_summary,
)
from agentic_layer.vectorize_service import embed_text, embed_texts
from agentic_layer.memory_manager import agentic_retrieve

EXTRACTION_BATCH_SIZE = 10
BATCH_SLEEP = 0

client = genai.Client(api_key=GEMINI_API_KEY)


def ingest_conversation(conversation: list[dict], source_id: str = "default",
                        current_date: str = None, interactive: bool = False,
                        extract_all_speakers: bool = False):
    """Ingestion pipeline: segmentation → extraction → storage → consolidation → post-pipeline."""
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
                executor.submit(extract_segment, seg, current_date, conv_summary): i
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
        batch_results = asyncio.run(
            store_batch(batch_extractions, episode_embeddings, source_id, interactive,
                        current_date=current_date)
        )
        all_results.extend(batch_results)
        print(f"       Batch total: {time.time() - extract_start:.1f}s")

    print(f"\n  Pipeline complete in {time.time() - pipeline_start:.1f}s")

    # Consolidation: group all atomic facts into consolidated facts
    consolidation_start = time.time()
    all_parsed_facts = []
    all_fact_ids = []
    for r in all_results:
        all_parsed_facts.extend(r.get("parsed_facts", []))
        all_fact_ids.extend(r.get("fact_ids", []))

    consolidated_entries = consolidate_facts(all_parsed_facts, all_fact_ids)

    # Store consolidated facts + embed + upsert to Qdrant
    if consolidated_entries:
        cf_texts = [e["consolidated_text"] for e in consolidated_entries]
        cf_embeddings = embed_texts(cf_texts)
        for entry, emb in zip(consolidated_entries, cf_embeddings):
            cf = ConsolidatedFact(
                consolidated_text=entry["consolidated_text"],
                fact_ids=entry["fact_ids"],
                metadata=entry.get("metadata", {}),
                source_id=source_id,
                conversation_date=current_date,
                embedding=emb,
            )
            cf_id = db.insert_consolidated_fact(cf)
            vector_store.upsert_consolidated_fact(cf_id, emb, current_date)
        print(f"  [consolidation] Stored {len(consolidated_entries)} consolidated facts in {time.time() - consolidation_start:.1f}s")

    # Post-pipeline: summary, session_summary, profile — all independent, run in parallel
    post_start = time.time()
    print(f"\n[post-pipeline] Running summary + session_summary + profile in parallel...")

    def _run_summary():
        t = time.time()
        rebuild_conversation_summary(all_new_episodes, current_date)
        return time.time() - t

    def _run_session_summary():
        t = time.time()
        build_session_summary(all_new_episodes, current_date, source_id)
        return time.time() - t

    def _run_profile():
        t = time.time()
        all_new_facts = [pf["text"] for r in all_results for pf in r.get("parsed_facts", [])]
        update_user_profile(new_facts=all_new_facts if all_new_facts else None)
        return time.time() - t

    with ThreadPoolExecutor(max_workers=3) as post_executor:
        summary_future = post_executor.submit(_run_summary)
        session_summary_future = post_executor.submit(_run_session_summary)
        profile_future = post_executor.submit(_run_profile)

        summary_time = summary_future.result()
        session_summary_time = session_summary_future.result()
        profile_time = profile_future.result()

    post_total = time.time() - post_start
    print(f"  [post-pipeline] Summary: {summary_time:.1f}s | Session: {session_summary_time:.1f}s | Profile: {profile_time:.1f}s | Total: {post_total:.1f}s (parallel)")

    total_memcells = len(all_results)
    total_conflicts = sum(r["conflicts"] for r in all_results)
    print(f"\nDone. Ingested {total_memcells} MemCells, {total_conflicts} conflicts detected, {len(consolidated_entries)} consolidated facts.")

    return [r["memcell_id"] for r in all_results]


def reset_databases():
    """Wipe and recreate all tables and Qdrant collections."""
    print("Resetting databases...")
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS consolidated_facts CASCADE;
        DROP TABLE IF EXISTS session_summaries CASCADE;
        DROP TABLE IF EXISTS conversation_summaries CASCADE;
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
    for name in ("facts", "scenes", "consolidated_facts"):
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
