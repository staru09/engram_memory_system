import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import db
import vector_store
from models import Foresight
from memory_layer.extractor import extract_from_conversation
from memory_layer.profile_extractor import update_user_profile, maybe_compress_profile
from memory_layer.summary_manager import append_to_rolling_summary, maybe_compress_summary
from agentic_layer.vectorize_service import embed_texts


def ingest_conversation(conversation: list[dict], source_id: str = "default",
                        current_date: str = None, interactive: bool = False,
                        extract_all_speakers: bool = False,
                        force_profile_update: bool = False):
    """Ingestion pipeline: extract → facts (PG + Qdrant) + summary + foresight + profile (every 5th)."""
    IST = timezone(timedelta(hours=5, minutes=30))
    if current_date is None:
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    # Extract conversation time from first message's created_at
    conversation_time = None
    first_msg = conversation[0] if conversation else None
    if first_msg and first_msg.get("created_at"):
        ts = first_msg["created_at"]
        if hasattr(ts, 'astimezone'):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            conversation_time = ts.astimezone(IST).strftime("%I:%M %p").lstrip("0")
        elif isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                conversation_time = dt.astimezone(IST).strftime("%-I:%M %p")
            except ValueError:
                pass

    pipeline_start = time.time()

    # Get existing summary for coreference resolution context
    summary = db.get_conversation_summary()
    existing_context = ""
    if summary["archive_text"]:
        existing_context += summary["archive_text"] + "\n"
    if summary["recent_text"]:
        existing_context += summary["recent_text"]

    if existing_context:
        print(f"[context] Using rolling summary ({len(existing_context)} chars)")

    # Step 1: Extract facts + foresight (1 LLM call)
    print(f"[extract] Extracting from {len(conversation)} turns...")
    t0 = time.time()
    result = extract_from_conversation(
        conversation, current_date, existing_context,
        extract_all_speakers=extract_all_speakers
    )
    facts = result["facts"]
    foresight = result["foresight"]
    print(f"  [extract] {len(facts)} facts, {len(foresight)} foresight ({time.time() - t0:.1f}s)")

    if not facts and not foresight:
        print(f"  [extract] Nothing extracted, skipping pipeline.")
        return

    # Get ingestion count
    ingestion_count = db.get_and_increment_ingestion_count()
    should_update_profile = True  # update profile every ingestion

    # Steps 2a + 2b + 2c + 3 (+ 4 if 5th ingestion) in parallel
    tasks = "facts (PG + Qdrant) + summary + foresight"
    if should_update_profile:
        tasks += f" + profile update (ingestion #{ingestion_count})"
    print(f"[post-extract] Running {tasks} in parallel...")
    post_start = time.time()

    def _run_pg_store():
        """Store facts + foresight in PostgreSQL (single connection)."""
        t = time.time()
        foresight_entries = [
            Foresight(
                description=fs["description"],
                valid_from=fs.get("valid_from"),
                valid_until=fs.get("valid_until"),
                evidence=fs.get("evidence", ""),
                duration_days=fs.get("duration_days"),
            )
            for fs in foresight
        ]
        fact_ids, expired = db.insert_facts_and_foresight(
            facts, foresight_entries, current_date,
            source_id=source_id, ingestion_number=ingestion_count
        )
        if expired:
            print(f"  [foresight] Expired {expired} entries")
        return time.time() - t, fact_ids

    def _run_facts_qdrant(fact_ids):
        """Embed and store facts in Qdrant."""
        t = time.time()
        fact_texts = [f["text"] for f in facts]
        embeddings = embed_texts(fact_texts)
        qdrant_facts = []
        for f, fid, emb in zip(facts, fact_ids, embeddings):
            qdrant_facts.append({
                "fact_id": fid,
                "embedding": emb,
                "fact_text": f["text"],
                "conversation_date": f.get("date", current_date),
                "category": f.get("category", "general"),
            })
        vector_store.upsert_facts_batch(qdrant_facts)
        return time.time() - t

    def _run_profile_update():
        t = time.time()
        profile, conflicts = update_user_profile(facts)
        return time.time() - t, len(conflicts)

    def _run_summary_append():
        t = time.time()
        append_to_rolling_summary(facts, current_date, conversation_time)
        return time.time() - t

    with ThreadPoolExecutor(max_workers=4) as executor:
        # Step 2a: PostgreSQL facts + foresight (single connection, must complete first for fact_ids)
        pg_future = executor.submit(_run_pg_store)
        pg_time, fact_ids = pg_future.result()

        # Steps 2b + 2c + 4 in parallel (2b needs fact_ids from 2a)
        qdrant_future = executor.submit(_run_facts_qdrant, fact_ids)
        summary_future = executor.submit(_run_summary_append)

        if should_update_profile:
            profile_future = executor.submit(_run_profile_update)

        qdrant_time = qdrant_future.result()
        summary_time = summary_future.result()

        if should_update_profile:
            profile_time, conflict_count = profile_future.result()
            print(f"  [post-extract] PG: {pg_time:.1f}s | Qdrant: {qdrant_time:.1f}s | Summary: {summary_time:.1f}s | Profile: {profile_time:.1f}s | Total: {time.time() - post_start:.1f}s")
        else:
            conflict_count = 0
            print(f"  [post-extract] PG: {pg_time:.1f}s | Qdrant: {qdrant_time:.1f}s | Summary: {summary_time:.1f}s | Total: {time.time() - post_start:.1f}s")

    # Step 5: Compression if needed
    maybe_compress_summary()
    if should_update_profile:
        maybe_compress_profile()

    total_time = time.time() - pipeline_start
    print(f"\nDone. {len(facts)} facts, {len(foresight)} foresight, {conflict_count} conflicts. Ingestion #{ingestion_count}. Total: {total_time:.1f}s")


def reset_databases():
    """Wipe and recreate all tables + Qdrant collections."""
    print("Resetting databases...")
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS query_logs CASCADE;
        DROP TABLE IF EXISTS conflict_log CASCADE;
        DROP TABLE IF EXISTS conversation_summaries CASCADE;
        DROP TABLE IF EXISTS facts CASCADE;
        DROP TABLE IF EXISTS foresight CASCADE;
        DROP TABLE IF EXISTS user_profile CASCADE;
        DROP TABLE IF EXISTS chat_messages CASCADE;
        DROP TABLE IF EXISTS chat_threads CASCADE;
    """)
    conn.commit()
    cur.close()
    db.release_connection(conn)

    # Reset Qdrant
    vector_store.delete_collection()
    vector_store.init_collections()

    db.init_schema()
    print("Databases reset.\n")
