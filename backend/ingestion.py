import threading
import time

import db
from main import ingest_conversation


INGESTION_MESSAGE_THRESHOLD = 20
PERIODIC_CHECK_INTERVAL = 600  # seconds
PERIODIC_MIN_MESSAGES = 4      

_ingestion_lock = threading.Lock()
_ingesting_threads: set[str] = set()


def run_background_ingestion(thread_id: str, messages: list[dict]):
    """Convert chat messages to conversation format and run existing pipeline."""
    try:
        conversation = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
        ]
        current_date = messages[-1]["created_at"].strftime("%Y-%m-%d")
        source_id = f"thread_{thread_id}_{current_date}"

        ingest_conversation(conversation, source_id=source_id, current_date=current_date)

        message_ids = [msg["id"] for msg in messages]
        db.mark_messages_ingested(message_ids)
        from agentic_layer.retrieval_utils import invalidate_foresight_cache
        invalidate_foresight_cache()
        print(f"[Background] Ingested {len(messages)} messages from thread {thread_id}")
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
