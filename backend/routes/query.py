import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter

import db
from models import QueryLog
from agentic_layer.memory_manager import agentic_retrieve
from backend.schemas import QueryRequest
from backend.gemini import call_gemini_with_tools
from backend.prompt import build_query_prompt

IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter()


def _date_str(val):
    """Convert date/datetime to ISO string for JSON serialization."""
    if val is None:
        return None
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    return str(val)


def _build_metadata(raw_result: dict, agentic_result: dict,
                    retrieval_time: float, llm_time: float) -> dict:
    """Serialize retrieval results into a JSON-safe metadata dict."""
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
                "memcell_id": e["memcell_id"],
                "episode_text": e.get("episode_text", ""),
                "relevance_score": e.get("relevance_score", 0),
                "semantic_sim": e.get("semantic_sim"),
                "staleness": e.get("staleness"),
                "conversation_date": _date_str(e.get("conversation_date")),
                "scene_id": e.get("scene_id"),
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
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "best_score": s["best_score"],
                "fact_ids": s.get("fact_ids", []),
            }
            for s in raw_result.get("scenes", [])
        ],
        "profile_included": raw_result.get("profile") is not None,
        "sufficiency": {
            "is_sufficient": agentic_result.get("is_sufficient", False),
            "rounds": agentic_result.get("rounds", 1),
            "reasoning": agentic_result.get("sufficiency", {}).get("reasoning", ""),
            "missing_information": agentic_result.get("sufficiency", {}).get("missing_information", []),
        },
        "timing": {
            "total_retrieval_s": round(retrieval_time, 3),
            "llm_response_s": round(llm_time, 3),
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

    # 3. Run agentic retrieval (no message storing, no ingestion)
    retrieval_start = time.time()
    agentic_result = agentic_retrieve(
        request.query,
        query_time=query_time.replace(tzinfo=None),
        verbose=True,
    )
    retrieval_time = time.time() - retrieval_start

    context = agentic_result["context"]
    raw_result = agentic_result["result"]

    # 4. Build query-specific prompt
    prompt = build_query_prompt(
        query=request.query,
        memory_context=context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 4. Call Gemini with tools
    llm_start = time.time()
    answer = call_gemini_with_tools(prompt)
    llm_time = time.time() - llm_start

    # 5. Build metadata
    metadata = _build_metadata(raw_result, agentic_result, retrieval_time, llm_time)

    # 6. Log to query_logs table
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
