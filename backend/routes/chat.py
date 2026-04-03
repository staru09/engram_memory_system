import json
import time as _time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from google import genai

import db
from config import GEMINI_MODEL
from agentic_layer.fetch_mem_service import retrieve, compose_context
from agentic_layer.profile_commands import handle_command
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

    # 1.5. Check for profile commands (remember/forget)
    cmd_response = handle_command(request.message)
    if cmd_response:
        db.insert_message(request.thread_id, "assistant", cmd_response)
        return {"response": cmd_response, "thread_id": request.thread_id}

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

    # 1.5. Check for profile commands (remember/forget)
    cmd_response = handle_command(request.message)
    if cmd_response:
        db.insert_message(request.thread_id, "assistant", cmd_response)

        async def cmd_generate():
            yield f"data: {json.dumps({'text': cmd_response})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

        return StreamingResponse(cmd_generate(), media_type="text/event-stream")

    pipeline_start = _time.time()
    query_time = datetime.now(IST)

    # 2 + 3. Fetch recent messages + memory context in parallel
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_recent():
        return db.get_unprocessed_messages(request.thread_id)

    def _fetch_memory():
        result = retrieve(request.message, query_time=query_time.replace(tzinfo=None))
        return compose_context(result)

    with ThreadPoolExecutor(max_workers=2) as executor:
        recent_future = executor.submit(_fetch_recent)
        memory_future = executor.submit(_fetch_memory)

        recent_messages = recent_future.result()
        db_time = _time.time() - pipeline_start

        memory_context = ""
        try:
            memory_context = memory_future.result()
        except Exception as e:
            print(f"[Chat Stream] Memory retrieval failed: {e}")
    retrieval_time = _time.time() - pipeline_start

    # 4. Build prompt
    prompt_start = _time.time()
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time,
    )
    prompt_time = _time.time() - prompt_start

    # 5. Stream from Gemini
    async def generate():
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
        total_time = _time.time() - pipeline_start

        timings = {
            "db_ms": round(db_time * 1000),
            "retrieval_ms": round(retrieval_time * 1000),
            "prompt_ms": round(prompt_time * 1000),
            "first_token_ms": round((first_token_time or 0) * 1000),
            "llm_ms": round(llm_total * 1000),
            "total_ms": round(total_time * 1000),
        }
        print(f"  [chat-stream] DB: {db_time*1000:.0f}ms | Retrieval: {retrieval_time*1000:.0f}ms | Prompt: {prompt_time*1000:.0f}ms | First token: {(first_token_time or 0)*1000:.0f}ms | LLM: {llm_total*1000:.0f}ms | Total: {total_time*1000:.0f}ms")

        answer = "".join(full_response)
        db.insert_message(request.thread_id, "assistant", answer)
        check_ingestion_trigger(request.thread_id)
        yield f"data: {json.dumps({'done': True, 'timings': timings})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
