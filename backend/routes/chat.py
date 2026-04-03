import json
import time as _time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

import db
from config import GEMINI_MODEL
from agentic_layer.fetch_mem_service import compose_chat_context
from agentic_layer.profile_commands import handle_command
from backend.schemas import ChatRequest
from backend.gemini import gemini_client
from backend.prompt import build_chat_prompt
from backend.ingestion import check_ingestion_trigger

IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
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

    # 2. Fetch all context in a single DB round-trip
    ctx = db.get_chat_context(request.thread_id, query_time.replace(tzinfo=None))
    recent_messages = ctx["recent_messages"]
    memory_context = compose_chat_context({
        "profile": ctx["profile"],
        "foresight": ctx["foresight"],
        "summary": ctx["summary"],
    })
    ctx_time = _time.time() - pipeline_start

    # 3. Build prompt
    prompt_start = _time.time()
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time,
    )
    prompt_time = _time.time() - prompt_start
    prompt_tokens = max(1, int(len(prompt) / 4))

    # 4. Stream from Gemini
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
            "ctx_ms": round(ctx_time * 1000),
            "prompt_ms": round(prompt_time * 1000),
            "first_token_ms": round((first_token_time or 0) * 1000),
            "llm_ms": round(llm_total * 1000),
            "total_ms": round(total_time * 1000),
        }
        print(f"  [chat] Context: {ctx_time*1000:.0f}ms | Tokens: {prompt_tokens} | First token: {(first_token_time or 0)*1000:.0f}ms | LLM: {llm_total*1000:.0f}ms | Total: {total_time*1000:.0f}ms")

        answer = "".join(full_response)
        db.insert_message(request.thread_id, "assistant", answer)
        check_ingestion_trigger(request.thread_id)
        yield f"data: {json.dumps({'done': True, 'timings': timings})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
