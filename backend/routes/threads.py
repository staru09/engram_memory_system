import uuid

from fastapi import APIRouter

import db
from backend.schemas import CreateThreadRequest

router = APIRouter()


@router.post("/threads")
def create_thread_endpoint(request: CreateThreadRequest = None):
    thread_id = str(uuid.uuid4())
    title = request.title if request else None
    db.create_thread(thread_id, title)
    return {"thread_id": thread_id}


@router.get("/threads")
def list_threads_endpoint(limit: int = 20):
    threads = db.list_threads(limit)
    return {"threads": threads}


@router.get("/threads/{thread_id}/messages")
def get_messages(thread_id: str, limit: int = 50, before_id: int = None):
    messages = db.get_thread_messages(thread_id, limit, before_id)
    has_more = len(messages) == limit
    return {"messages": messages, "has_more": has_more}
