import time
import asyncio
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
import vector_store
from models import ConsolidatedFact
from memory_layer.memcell_extractor import extract_segments
from memory_layer.episode_extractor import process_segment
from memory_layer.storage import store_batch
from memory_layer.consolidator import consolidate_facts
from memory_layer.profile_extractor import (
    update_user_profile, update_category_profiles, rebuild_conversation_summary,
)
from agentic_layer.vectorize_service import embed_texts
from agentic_layer.fetch_mem_service import invalidate_category_cache

EXTRACTION_BATCH_SIZE = 10
BATCH_SLEEP = 0


def ingest_conversation(conversation: list[dict], source_id: str = "default",
                        current_date: str = None, interactive: bool = False,
                        extract_all_speakers: bool = False):
    """Ingestion pipeline: segmentation → extraction → storage → post-pipeline."""
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    print(f"[segment] Segmenting conversation ({len(conversation)} turns)...")
    segments = extract_segments(conversation, extract_all_speakers=extract_all_speakers)
    print(f"  Found {len(segments)} segments.")

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
            time.sleep(BATCH_SLEEP)

        print(f"\n  -- Batch {batch_num}/{total_batches} --")
        print(f"  [extract] {len(batch)} segments in parallel...")
        extract_start = time.time()
        batch_extractions = [None] * len(batch)

        with ThreadPoolExecutor(max_workers=EXTRACTION_BATCH_SIZE) as executor:
            future_to_idx = {
                executor.submit(process_segment, seg, current_date, conv_summary): i
                for i, seg in enumerate(batch)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                batch_extractions[idx] = future.result()
                seg_info = batch_extractions[idx]
                print(f"    {seg_info['segment']['topic_hint']} "
                      f"({len(seg_info['atomic_facts'])} facts, {len(seg_info['foresight'])} foresight)")

        for ext in batch_extractions:
            all_new_episodes.append(ext["episode_text"])

        print(f"       Extraction: {time.time() - extract_start:.1f}s")

        episode_texts = [ext["episode_text"] for ext in batch_extractions]
        episode_embeddings = embed_texts(episode_texts)

        batch_results = asyncio.run(
            store_batch(batch_extractions, episode_embeddings, source_id, interactive,
                        current_date=current_date)
        )
        all_results.extend(batch_results)
        print(f"       Batch total: {time.time() - extract_start:.1f}s")

    print(f"\n  Pipeline complete in {time.time() - pipeline_start:.1f}s")

    # Collect facts for post-pipeline
    new_facts_by_category = {}
    all_parsed_facts = []
    all_fact_ids = []
    for r in all_results:
        for pf in r.get("parsed_facts", []):
            cat = pf.get("category", "general")
            if cat not in new_facts_by_category:
                new_facts_by_category[cat] = []
            new_facts_by_category[cat].append(pf["text"])
        all_parsed_facts.extend(r.get("parsed_facts", []))
        all_fact_ids.extend(r.get("fact_ids", []))

    # Post-pipeline: all independent, run in parallel
    post_start = time.time()
    print(f"\n[post-pipeline] Running summary + profile + categories + consolidation in parallel...")

    def _run_summary():
        t = time.time()
        rebuild_conversation_summary(all_new_episodes, current_date)
        return time.time() - t

    def _run_profile():
        t = time.time()
        all_new_facts = [pf["text"] for pf in all_parsed_facts]
        update_user_profile(new_facts=all_new_facts if all_new_facts else None)
        return time.time() - t

    def _run_categories():
        t = time.time()
        if new_facts_by_category:
            update_category_profiles(new_facts_by_category)
            invalidate_category_cache()
        return time.time() - t

    def _run_consolidation():
        t = time.time()
        if not all_parsed_facts:
            return 0.0
        entries = consolidate_facts(all_parsed_facts, all_fact_ids)
        if entries:
            cf_texts = [e["consolidated_text"] for e in entries]
            cf_embeddings = embed_texts(cf_texts)
            for entry, emb in zip(entries, cf_embeddings):
                cf = ConsolidatedFact(
                    consolidated_text=entry["consolidated_text"],
                    fact_ids=entry["fact_ids"],
                    source_id=source_id,
                    conversation_date=current_date,
                    embedding=emb,
                )
                cf_id = db.insert_consolidated_fact(cf)
                vector_store.upsert_consolidated_fact(cf_id, emb, current_date)
            print(f"  [consolidation] Stored {len(entries)} consolidated facts")
        return time.time() - t

    with ThreadPoolExecutor(max_workers=4) as post_executor:
        summary_future = post_executor.submit(_run_summary)
        profile_future = post_executor.submit(_run_profile)
        category_future = post_executor.submit(_run_categories)
        consolidation_future = post_executor.submit(_run_consolidation)

        summary_time = summary_future.result()
        profile_time = profile_future.result()
        category_time = category_future.result()
        consolidation_time = consolidation_future.result()

    post_total = time.time() - post_start
    print(f"  [post-pipeline] Summary: {summary_time:.1f}s | Profile: {profile_time:.1f}s | Categories: {category_time:.1f}s | Consolidation: {consolidation_time:.1f}s | Total: {post_total:.1f}s (parallel)")

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
        DROP TABLE IF EXISTS consolidated_facts CASCADE;
        DROP TABLE IF EXISTS conversation_summaries CASCADE;
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
    from config import QDRANT_HOST, QDRANT_PORT, QDRANT_URL, QDRANT_API_KEY
    if QDRANT_URL:
        qclient = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
    else:
        qclient = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False, timeout=30)
    for name in ("facts", "scenes", "consolidated_facts"):
        if qclient.collection_exists(name):
            qclient.delete_collection(name)

    db.init_schema()
    vector_store.init_collections()
    print("Databases reset.\n")
