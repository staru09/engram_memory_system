import json
import time as _time
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
    # 0. Ensure thread exists
    if not db.get_thread(request.thread_id):
        db.create_thread(request.thread_id)

    # 1. Store user message
    db.insert_message(request.thread_id, "user", request.message)

    # 2. Get unindexed messages as short-term memory
    query_time = datetime.now(IST)
    recent_messages = db.get_unprocessed_messages(request.thread_id)

    # 3. Retrieve memory context (always fast — profile + category summaries)
    memory_context = ""
    try:
        result = retrieve(request.message, query_time=query_time.replace(tzinfo=None))
        memory_context = compose_context(result)
    except Exception as e:
        print(f"[Chat] Memory retrieval failed: {e}")

    # 4. Build prompt
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 5. Call Gemini with time calculator tool
    llm_start = _time.time()
    answer = call_gemini_with_tools(prompt)
    print(f"  [chat] LLM: {_time.time() - llm_start:.1f}s")

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
    query_time = datetime.now(IST)
    recent_messages = db.get_unprocessed_messages(request.thread_id)

    # 3. Retrieve memory context (always fast — profile + category summaries)
    memory_context = ""
    try:
        result = retrieve(request.message, query_time=query_time.replace(tzinfo=None))
        memory_context = compose_context(result)
    except Exception as e:
        print(f"[Chat Stream] Memory retrieval failed: {e}")

    # 4. Build prompt
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 5. Stream from Gemini
    async def generate():
        import time as _time
        llm_start = _time.time()
        response = gemini_client.models.generate_content_stream(
            model=GEMINI_MODEL, contents=prompt
        )
        full_response = []
        first_token_time = None
        for chunk in response:
            if first_token_time is None:
                first_token_time = _time.time() - llm_start
            text = chunk.text
            if text is None:
                continue
            full_response.append(text)
            yield f"data: {json.dumps({'text': text})}\n\n"

        llm_total = _time.time() - llm_start
        print(f"  [chat-stream] LLM: {llm_total:.1f}s (first token: {first_token_time:.1f}s)")

        answer = "".join(full_response)
        db.insert_message(request.thread_id, "assistant", answer)
        check_ingestion_trigger(request.thread_id)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
