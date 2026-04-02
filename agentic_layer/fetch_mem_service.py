import time
import db
import vector_store
from agentic_layer.vectorize_service import embed_text
from agentic_layer.temporal_parser import parse_temporal_query
from config import RETRIEVAL_TOP_K


def hybrid_search_facts(query: str, top_k: int = RETRIEVAL_TOP_K,
                        date_filter: dict = None, query_embedding: list[float] = None) -> list[dict]:
    """Hybrid search: keyword (PostgreSQL) + vector (Qdrant) → RRF fusion → top-k facts."""
    if query_embedding is None:
        query_embedding = embed_text(query)

    # Parallel search
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as executor:
        kw_future = executor.submit(db.keyword_search_facts, query, top_k * 2, date_filter)
        vec_future = executor.submit(vector_store.search_facts, query_embedding, top_k * 2, date_filter)

        kw_results = kw_future.result()
        vec_results = vec_future.result()

    # RRF fusion (k=60)
    K = 60
    KEYWORD_WEIGHT = 1.5
    VECTOR_WEIGHT = 1.0

    scores = {}
    fact_map = {}

    for rank, r in enumerate(kw_results):
        fid = r["id"]
        scores[fid] = scores.get(fid, 0) + KEYWORD_WEIGHT / (K + rank + 1)
        fact_map[fid] = {
            "fact_id": fid,
            "fact_text": r["fact_text"],
            "conversation_date": r.get("conversation_date"),
            "category": r.get("category"),
        }

    for rank, r in enumerate(vec_results):
        fid = r["fact_id"]
        scores[fid] = scores.get(fid, 0) + VECTOR_WEIGHT / (K + rank + 1)
        if fid not in fact_map:
            fact_map[fid] = {
                "fact_id": fid,
                "fact_text": r["fact_text"],
                "conversation_date": r.get("conversation_date"),
                "category": r.get("category"),
            }

    # Sort by RRF score, return top-k
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    results = []
    for fid, score in ranked:
        entry = fact_map[fid]
        entry["rrf_score"] = score
        results.append(entry)

    return results


def retrieve_for_chat(query: str, query_time=None) -> dict:
    """Chat retrieval: rolling summary + profile + foresight. No search."""
    profile = db.get_user_profile()
    foresight = db.get_active_foresight(query_time) if query_time else []
    summary = db.get_conversation_summary()

    return {
        "profile": profile,
        "foresight": foresight,
        "summary": summary,
        "facts": [],
    }


def retrieve_for_query(query: str, query_time=None) -> dict:
    """Query retrieval: hybrid search facts + profile + foresight + temporal parse."""
    t0 = time.time()
    timings = {}

    # Step 1: Temporal parse + embed query (parallel)
    from concurrent.futures import ThreadPoolExecutor
    reference_date = str(query_time) if query_time else None

    def _timed_temporal():
        t = time.time()
        result = parse_temporal_query(query, reference_date)
        return result, round(time.time() - t, 3)

    def _timed_embed():
        t = time.time()
        result = embed_text(query)
        return result, round(time.time() - t, 3)

    with ThreadPoolExecutor(max_workers=2) as executor:
        temporal_future = executor.submit(_timed_temporal)
        embed_future = executor.submit(_timed_embed)

        temporal_result, timings["temporal_parse_s"] = temporal_future.result()
        query_embedding, timings["embed_s"] = embed_future.result()

    # Step 2: Build date filter from temporal parse
    date_filter = None
    if temporal_result:
        date_filter = {
            "date_from": temporal_result["date_from"],
            "date_to": temporal_result["date_to"],
        }
        print(f"  [query-retrieval] Temporal: {date_filter['date_from']} to {date_filter['date_to']}")

    # Step 3: Hybrid search + profile + foresight (all parallel)
    def _timed_search():
        t = time.time()
        result = hybrid_search_facts(query, RETRIEVAL_TOP_K, date_filter, query_embedding)
        return result, round(time.time() - t, 3)

    def _timed_profile():
        t = time.time()
        result = db.get_user_profile()
        return result, round(time.time() - t, 3)

    def _timed_foresight():
        t = time.time()
        result = db.get_active_foresight(query_time) if query_time else []
        return result, round(time.time() - t, 3)

    with ThreadPoolExecutor(max_workers=3) as executor:
        facts_future = executor.submit(_timed_search)
        profile_future = executor.submit(_timed_profile)
        foresight_future = executor.submit(_timed_foresight)

        facts, timings["hybrid_search_s"] = facts_future.result()
        profile, timings["profile_s"] = profile_future.result()
        foresight, timings["foresight_s"] = foresight_future.result()

    timings["total_retrieval_s"] = round(time.time() - t0, 3)
    print(f"  [query-retrieval] {len(facts)} facts | temporal: {timings['temporal_parse_s']}s | embed: {timings['embed_s']}s | search: {timings['hybrid_search_s']}s | profile: {timings['profile_s']}s | foresight: {timings['foresight_s']}s | total: {timings['total_retrieval_s']}s")

    return {
        "profile": profile,
        "foresight": foresight,
        "summary": {},
        "facts": facts,
        "timings": timings,
    }


def compose_chat_context(result: dict) -> str:
    """Compose context for chat: summary + foresight + profile."""
    parts = []

    # Rolling summary
    summary = result.get("summary", {})
    archive = summary.get("archive_text", "")
    recent = summary.get("recent_text", "")
    if archive or recent:
        parts.append("=== CONVERSATION HISTORY ===")
        if archive:
            parts.append("[Archive]")
            parts.append(archive)
            parts.append("")
        if recent:
            parts.append("[Recent]")
            parts.append(recent)
        parts.append("")

    # Foresight
    foresight = result.get("foresight", [])
    if foresight:
        parts.append("=== UPCOMING / TIME-BOUNDED ===")
        for fs in foresight:
            until = fs.get("valid_until")
            until_str = str(until) if until else "indefinite"
            evidence = fs.get("evidence")
            evidence_str = f" [source: {evidence}]" if evidence else ""
            parts.append(f"- {fs['description']} (valid until: {until_str}){evidence_str}")
        parts.append("")

    # Profile
    profile = result.get("profile", "")
    if profile:
        parts.append("=== USER PROFILE ===")
        parts.append(profile)

    return "\n".join(parts)


def compose_query_context(result: dict) -> str:
    """Compose context for query: matched facts + foresight + profile."""
    parts = []

    # Matched facts first
    facts = result.get("facts", [])
    if facts:
        parts.append("=== MATCHED FACTS ===")
        for f in facts:
            date_tag = f" [{f['conversation_date']}]" if f.get("conversation_date") else ""
            parts.append(f"- {f['fact_text']}{date_tag}")
        parts.append("")

    # Foresight
    foresight = result.get("foresight", [])
    if foresight:
        parts.append("=== UPCOMING / TIME-BOUNDED ===")
        for fs in foresight:
            until = fs.get("valid_until")
            until_str = str(until) if until else "indefinite"
            evidence = fs.get("evidence")
            evidence_str = f" [source: {evidence}]" if evidence else ""
            parts.append(f"- {fs['description']} (valid until: {until_str}){evidence_str}")
        parts.append("")

    # Profile
    profile = result.get("profile", "")
    if profile:
        parts.append("=== USER PROFILE ===")
        parts.append(profile)

    return "\n".join(parts)


# Backward compatibility
def retrieve(query: str, query_time=None) -> dict:
    return retrieve_for_chat(query, query_time)


def compose_context(result: dict) -> str:
    return compose_chat_context(result)
