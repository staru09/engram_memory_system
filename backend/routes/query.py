import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter

import db
from models import QueryLog
from agentic_layer.query_classifier import classify_query
from agentic_layer.fetch_mem_service import retrieve_simple, retrieve_fast, compose_context, compose_context_fast
from agentic_layer.vectorize_service import embed_text
from backend.schemas import QueryRequest
from backend.gemini import call_gemini_with_tools
from backend.prompt import build_query_prompt

IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter()


def _date_str(val):
    if val is None:
        return None
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    return str(val)


def _build_metadata(raw_result: dict, classification: dict,
                    retrieval_time: float, llm_time: float) -> dict:
    retrieval_timing = raw_result.get("timing", {})
    return {
        "facts": [
            {
                "fact_id": f["fact_id"],
                "fact_text": f.get("fact_text", ""),
                "rrf_score": f.get("rrf_score", 0),
                "conversation_date": _date_str(f.get("conversation_date")),
                "memcell_id": f.get("memcell_id"),
            }
            for f in raw_result.get("facts", [])
        ],
        "episodes": [
            {
                "memcell_id": e.get("id", e.get("memcell_id")),
                "episode_text": e.get("episode_text", ""),
                "conversation_date": _date_str(e.get("conversation_date")),
            }
            for e in raw_result.get("episodes", [])
        ],
        "foresight": [
            {
                "id": fs.get("id"),
                "description": fs.get("description", ""),
                "valid_from": _date_str(fs.get("valid_from")),
                "valid_until": _date_str(fs.get("valid_until")),
                "source_date": _date_str(fs.get("source_date")),
                "query_sim": fs.get("query_sim"),
            }
            for fs in raw_result.get("foresight", [])
        ],
        "categories_matched": classification.get("categories", []),
        "complexity": classification.get("complexity", "COMPLEX"),
        "profile_included": raw_result.get("profile") is not None,
        "timing": {
            "total_retrieval_s": round(retrieval_time, 3),
            "llm_response_s": round(llm_time, 3),
            "classifier_s": classification.get("classifier_s", 0),
            "embedding_s": retrieval_timing.get("embedding_s", 0),
            "search_s": retrieval_timing.get("search_s", 0),
            "foresight_s": retrieval_timing.get("foresight_s", 0),
            "context_compose_s": retrieval_timing.get("context_compose_s", 0),
        },
    }


@router.post("/query")
def query_memory(request: QueryRequest):
    query_time = datetime.now(IST)

    # 1. Check if any memories exist
    stats = db.get_system_stats()
    if stats["active_facts"] == 0:
        return {
            "response": "Abhi toh koi memory nahi hai yaar.",
            "metadata": {},
            "query_time": query_time.isoformat(),
        }

    # 2. Fetch short-term memory (unprocessed messages from thread)
    recent_messages = []
    if request.thread_id:
        recent_messages = db.get_unprocessed_messages(request.thread_id)

    # 3. Classify query
    classification = classify_query(request.query)
    complexity = classification.get("complexity", "COMPLEX")
    categories = classification.get("categories", [])
    print(f"  [query] Classified as {complexity}, categories: {categories} ({classification.get('classifier_s', 0)}s)")

    # 4. Route to appropriate retrieval tier (retrieval time excludes classifier)
    retrieval_start = time.time()
    if complexity == "NONE":
        raw_result = retrieve_simple(request.query, categories=categories,
                                     query_time=query_time.replace(tzinfo=None))
        context = compose_context_fast(raw_result)
    elif complexity == "SIMPLE":
        raw_result = retrieve_simple(request.query, categories=categories,
                                     query_time=query_time.replace(tzinfo=None))
        context = compose_context_fast(raw_result)
    else:
        query_embedding = embed_text(request.query)
        raw_result = retrieve_fast(request.query,
                                   query_time=query_time.replace(tzinfo=None),
                                   categories=categories,
                                   query_embedding=query_embedding)
        context = compose_context(raw_result)

    retrieval_time = time.time() - retrieval_start

    # 5. Build query-specific prompt
    prompt = build_query_prompt(
        query=request.query,
        memory_context=context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 6. Call Gemini with tools
    llm_start = time.time()
    answer = call_gemini_with_tools(prompt)
    llm_time = time.time() - llm_start

    # 7. Build metadata
    metadata = _build_metadata(raw_result, classification, retrieval_time, llm_time)

    # 8. Log to query_logs table
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
