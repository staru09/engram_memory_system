import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import db
from models import Foresight
from memory_layer.extractor import extract_from_conversation
from memory_layer.profile_extractor import (
    update_user_profile, append_to_rolling_summary,
    maybe_compress_profile, maybe_compress_summary,
)


def ingest_conversation(conversation: list[dict], source_id: str = "default",
                        current_date: str = None, interactive: bool = False,
                        extract_all_speakers: bool = False):
    """Ingestion pipeline: extract → profile update → foresight → rolling summary → compress."""
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

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

    # Steps 2 + 3 + 4 in parallel
    print(f"[post-extract] Running profile update + foresight + summary append in parallel...")
    post_start = time.time()

    def _run_profile_update():
        t = time.time()
        profile, conflicts = update_user_profile(facts)
        return time.time() - t, len(conflicts)

    def _run_foresight():
        t = time.time()
        # Expire old foresight
        expired = db.expire_foresight(current_date)
        if expired:
            print(f"  [foresight] Expired {expired} entries")
        # Store new foresight
        for fs in foresight:
            db.insert_foresight(Foresight(
                description=fs["description"],
                valid_from=fs.get("valid_from"),
                valid_until=fs.get("valid_until"),
            ))
        return time.time() - t

    def _run_summary_append():
        t = time.time()
        append_to_rolling_summary(facts, current_date)
        return time.time() - t

    with ThreadPoolExecutor(max_workers=3) as executor:
        profile_future = executor.submit(_run_profile_update)
        foresight_future = executor.submit(_run_foresight)
        summary_future = executor.submit(_run_summary_append)

        profile_time, conflict_count = profile_future.result()
        foresight_time = foresight_future.result()
        summary_time = summary_future.result()

    print(f"  [post-extract] Profile: {profile_time:.1f}s | Foresight: {foresight_time:.1f}s | Summary: {summary_time:.1f}s | Total: {time.time() - post_start:.1f}s (parallel)")

    # Steps 5 + 6: Compression if needed (only when 80% cap hit)
    maybe_compress_summary()
    maybe_compress_profile()

    total_time = time.time() - pipeline_start
    print(f"\nDone. {len(facts)} facts, {len(foresight)} foresight, {conflict_count} conflicts. Total: {total_time:.1f}s")


def reset_databases():
    """Wipe and recreate all tables."""
    print("Resetting databases...")
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS query_logs CASCADE;
        DROP TABLE IF EXISTS conflict_log CASCADE;
        DROP TABLE IF EXISTS conversation_summaries CASCADE;
        DROP TABLE IF EXISTS foresight CASCADE;
        DROP TABLE IF EXISTS user_profile CASCADE;
        DROP TABLE IF EXISTS chat_messages CASCADE;
        DROP TABLE IF EXISTS chat_threads CASCADE;
    """)
    conn.commit()
    cur.close()
    db.release_connection(conn)

    db.init_schema()
    print("Databases reset.\n")
