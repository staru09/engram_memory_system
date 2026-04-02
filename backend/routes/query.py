import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter

import db
from models import QueryLog
from agentic_layer.fetch_mem_service import retrieve_for_query, compose_query_context
from backend.schemas import QueryRequest
from backend.gemini import call_gemini_with_tools
from backend.prompt import build_query_prompt

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

    # 2. Fetch short-term memory (unprocessed messages from thread)
    recent_messages = []
    if request.thread_id:
        recent_messages = db.get_unprocessed_messages(request.thread_id)

    # 3. Retrieve
    retrieval_start = time.time()
    raw_result = retrieve_for_query(request.query, query_time=query_time.replace(tzinfo=None))
    context = compose_query_context(raw_result)
    retrieval_time = time.time() - retrieval_start
    print(f"  [query] Retrieval: {retrieval_time:.2f}s")

    # 4. Build query-specific prompt
    prompt = build_query_prompt(
        query=request.query,
        memory_context=context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 5. Call Gemini with tools
    llm_start = time.time()
    answer = call_gemini_with_tools(prompt)
    llm_time = time.time() - llm_start
    print(f"  [query] LLM: {llm_time:.1f}s")

    # 6. Log to query_logs table
    retrieval_timings = raw_result.get("timings", {})
    metadata = {
        "timing": {
            **retrieval_timings,
            "llm_response_s": round(llm_time, 3),
        },
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
