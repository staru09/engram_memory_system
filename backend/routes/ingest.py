import threading

from fastapi import APIRouter

import db
from backend.ingestion import (
    run_background_ingestion, _ingestion_lock, _ingesting_threads
)

router = APIRouter()


@router.post("/threads/{thread_id}/ingest")
def trigger_ingestion(thread_id: str):
    """Manually trigger ingestion for a thread."""
    unprocessed = db.get_unprocessed_messages(thread_id)
    if not unprocessed:
        return {"status": "no_unprocessed_messages", "count": 0}

    with _ingestion_lock:
        if thread_id in _ingesting_threads:
            return {"status": "already_ingesting"}
        _ingesting_threads.add(thread_id)

    threading.Thread(
        target=run_background_ingestion,
        args=(thread_id, unprocessed),
        daemon=True
    ).start()

    return {"status": "ingestion_started", "message_count": len(unprocessed)}
