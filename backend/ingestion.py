import threading
import time
from datetime import timezone, timedelta

import db
from main import ingest_conversation

IST = timezone(timedelta(hours=5, minutes=30))


INGESTION_MESSAGE_THRESHOLD = 20
PERIODIC_CHECK_INTERVAL = 600  # seconds
PERIODIC_MIN_MESSAGES = 4      

_ingestion_lock = threading.Lock()
_ingesting_threads: set[str] = set()


INGESTION_BATCH_SIZE = 20  # process N messages at a time


def run_background_ingestion(thread_id: str, messages: list[dict]):
    """Convert chat messages to conversation format and run existing pipeline in batches."""
    try:
        total = len(messages)
        num_batches = (total + INGESTION_BATCH_SIZE - 1) // INGESTION_BATCH_SIZE
        print(f"[Background] Ingesting {total} messages in {num_batches} batches of {INGESTION_BATCH_SIZE}")

        for batch_idx in range(0, total, INGESTION_BATCH_SIZE):
            batch = messages[batch_idx:batch_idx + INGESTION_BATCH_SIZE]
            batch_num = (batch_idx // INGESTION_BATCH_SIZE) + 1

            conversation = [
                {"role": msg["role"], "content": msg["content"], "created_at": msg.get("created_at")}
                for msg in batch
            ]

            last_ts = batch[-1]["created_at"]
            if hasattr(last_ts, 'tzinfo') and last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            current_date = last_ts.astimezone(IST).strftime("%Y-%m-%d")
            source_id = f"thread_{thread_id}_{current_date}_batch{batch_num}"

            print(f"\n[Background] === Batch {batch_num}/{num_batches} ({len(batch)} messages, date: {current_date}) ===")
            ingest_conversation(conversation, source_id=source_id, current_date=current_date)

            # Mark this batch's messages as ingested
            batch_ids = [msg["id"] for msg in batch]
            db.mark_messages_ingested(batch_ids)

        print(f"\n[Background] Done. Ingested {total} messages from thread {thread_id}")
    except Exception as e:
        print(f"[Background] Ingestion failed for thread {thread_id}: {e}")
    finally:
        with _ingestion_lock:
            _ingesting_threads.discard(thread_id)


def check_ingestion_trigger(thread_id: str):
    """Trigger background ingestion if unprocessed message count exceeds threshold."""
    with _ingestion_lock:
        if thread_id in _ingesting_threads:
            return
    unprocessed = db.get_unprocessed_messages(thread_id)
    if len(unprocessed) >= INGESTION_MESSAGE_THRESHOLD:
        with _ingestion_lock:
            if thread_id in _ingesting_threads:
                return
            _ingesting_threads.add(thread_id)
        threading.Thread(
            target=run_background_ingestion,
            args=(thread_id, unprocessed),
            daemon=True
        ).start()


def periodic_ingestion_check():
    """Periodically check for threads with old unprocessed messages."""
    while True:
        time.sleep(PERIODIC_CHECK_INTERVAL)
        try:
            thread_ids = db.get_threads_with_old_unprocessed(minutes=10)
            for thread_id in thread_ids:
                with _ingestion_lock:
                    if thread_id in _ingesting_threads:
                        continue
                unprocessed = db.get_unprocessed_messages(thread_id)
                if len(unprocessed) >= PERIODIC_MIN_MESSAGES:
                    with _ingestion_lock:
                        _ingesting_threads.add(thread_id)
                    threading.Thread(
                        target=run_background_ingestion,
                        args=(thread_id, unprocessed),
                        daemon=True
                    ).start()
        except Exception as e:
            print(f"[Periodic] Check failed: {e}")
