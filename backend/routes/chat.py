import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from google import genai

import db
from config import GEMINI_MODEL
from agentic_layer.fetch_mem_service import retrieve, compose_context
from backend.schemas import ChatRequest
from backend.gemini import gemini_client, call_gemini_with_tools
from backend.prompt import build_chat_prompt
from backend.ingestion import check_ingestion_trigger

IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter()


@router.post("/chat")
def chat(request: ChatRequest):
    # 0. Ensure thread exists (handles stale localStorage after DB reset)
    if not db.get_thread(request.thread_id):
        db.create_thread(request.thread_id)

    # 1. Store user message
    db.insert_message(request.thread_id, "user", request.message)

    # 2. Get unindexed messages as short-term memory
    query_time_utc = datetime.now(timezone.utc)
    query_time_ist = query_time_utc.astimezone(IST)
    recent_messages = db.get_unprocessed_messages(request.thread_id)

    # 3. Retrieve memory context (long-term memory) — use UTC for DB comparisons
    memory_context = ""
    try:
        stats = db.get_system_stats()
        if stats["active_facts"] > 0:
            result = retrieve(request.message, query_time_utc.replace(tzinfo=None))
            memory_context = compose_context(result)
    except Exception as e:
        print(f"[Chat] Memory retrieval failed: {e}")

    # 4. Build prompt — use IST for display
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time_ist,
    )

    # 5. Call Gemini with time calculator tool
    answer = call_gemini_with_tools(prompt)

    # 6. Store assistant response
    db.insert_message(request.thread_id, "assistant", answer)

    # 7. Check if background ingestion should trigger
    check_ingestion_trigger(request.thread_id)

    return {"response": answer, "thread_id": request.thread_id}


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    # 0. Ensure thread exists
    if not db.get_thread(request.thread_id):
        db.create_thread(request.thread_id)

    # 1. Store user message
    db.insert_message(request.thread_id, "user", request.message)

    # 2. Get unindexed messages as short-term memory
    query_time_utc = datetime.now(timezone.utc)
    query_time_ist = query_time_utc.astimezone(IST)
    recent_messages = db.get_unprocessed_messages(request.thread_id)

    memory_context = ""
    try:
        stats = db.get_system_stats()
        if stats["active_facts"] > 0:
            result = retrieve(request.message, query_time_utc.replace(tzinfo=None))
            memory_context = compose_context(result)
    except Exception as e:
        print(f"[Chat Stream] Memory retrieval failed: {e}")

    # 3. Build prompt — use IST for display
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time_ist,
    )

    # 4. Stream from Gemini
    async def generate():
        response = gemini_client.models.generate_content_stream(
            model=GEMINI_MODEL, contents=prompt
        )
        full_response = []
        for chunk in response:
            text = chunk.text
            full_response.append(text)
            yield f"data: {json.dumps({'text': text})}\n\n"

        answer = "".join(full_response)
        db.insert_message(request.thread_id, "assistant", answer)
        check_ingestion_trigger(request.thread_id)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
