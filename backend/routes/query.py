import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter

import db
from models import QueryLog
from agentic_layer.fetch_mem_service import retrieve_for_query, compose_query_context
from backend.schemas import QueryRequest
from backend.gemini import call_gemini_with_tools, gemini_client
from backend.prompt import build_query_prompt
from config import GEMINI_MODEL

IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter()


@router.post("/query")
def query_memory(request: QueryRequest):
    query_time = datetime.now(IST)

    # 1. Check if any memories exist
    stats = db.get_system_stats()
    if not stats["has_profile"]:
        return {
            "response": "Abhi toh koi memory nahi hai yaar.",
            "metadata": {},
            "query_time": query_time.isoformat(),
        }

    # 2. Retrieve (includes unprocessed messages in same DB call)
    retrieval_start = time.time()
    raw_result = retrieve_for_query(request.query, query_time=query_time.replace(tzinfo=None), thread_id=request.thread_id)
    context = compose_query_context(raw_result)
    retrieval_time = time.time() - retrieval_start
    print(f"  [query] Retrieval: {retrieval_time:.2f}s")

    # 4. Build query-specific prompt
    prompt = build_query_prompt(
        query=request.query,
        memory_context=context,
        recent_messages=raw_result.get("recent_messages", []),
        query_time=query_time,
    )

    # 5. Count tokens + call Gemini with tools
    try:
        token_count = gemini_client.models.count_tokens(model=GEMINI_MODEL, contents=prompt).total_tokens
    except Exception:
        token_count = max(1, int(len(prompt) / 3.5))

    llm_start = time.time()
    answer = call_gemini_with_tools(prompt)
    llm_time = time.time() - llm_start
    print(f"  [query] Tokens: {token_count} | LLM: {llm_time:.1f}s")

    # 6. Log to query_logs table
    retrieval_timings = raw_result.get("timings", {})
    metadata = {
        "timing": {
            **retrieval_timings,
            "llm_response_s": round(llm_time, 3),
        },
        "prompt_tokens": token_count,
    }
    try:
        log = QueryLog(
            thread_id=request.thread_id,
            query_text=request.query,
            response_text=answer,
            memory_context=context,
            retrieval_metadata=metadata,
            query_time=query_time.replace(tzinfo=None),
        )
        db.insert_query_log(log)
    except Exception as e:
        print(f"[Query] Failed to log query: {e}")

    return {
        "response": answer,
        "metadata": metadata,
        "query_time": query_time.isoformat(),
    }
