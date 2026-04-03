import time
import db
import vector_store
from agentic_layer.vectorize_service import embed_text
from agentic_layer.temporal_parser import parse_temporal_query
from config import RETRIEVAL_TOP_K


def hybrid_search_facts(query_embedding: list[float], kw_results: list[dict],
                        top_k: int = RETRIEVAL_TOP_K, date_filter: dict = None) -> list[dict]:
    """Hybrid search: keyword results (pre-fetched from PG) + vector (Qdrant) → RRF fusion → top-k facts."""

    # Vector search (only external call — keyword already done in get_query_context)
    vec_results = vector_store.search_facts(query_embedding, top_k * 2, date_filter)

    # RRF fusion (k=60)
    # Vector weighted higher — users query in Hinglish, facts stored in English.
    # Embeddings bridge the language gap, keywords can't.
    K = 60
    KEYWORD_WEIGHT = 0.5
    VECTOR_WEIGHT = 1.5

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
    """Chat retrieval: rolling summary + profile + foresight. No search, parallel DB reads."""
    from concurrent.futures import ThreadPoolExecutor

    def _profile():
        t = time.time()
        r = db.get_user_profile()
        print(f"    [chat-retrieve] profile: {(time.time()-t)*1000:.0f}ms")
        return r

    def _foresight():
        t = time.time()
        r = db.get_active_foresight(query_time) if query_time else []
        print(f"    [chat-retrieve] foresight: {(time.time()-t)*1000:.0f}ms")
        return r

    def _summary():
        t = time.time()
        r = db.get_conversation_summary()
        print(f"    [chat-retrieve] summary: {(time.time()-t)*1000:.0f}ms")
        return r

    with ThreadPoolExecutor(max_workers=3) as executor:
        profile_f = executor.submit(_profile)
        foresight_f = executor.submit(_foresight)
        summary_f = executor.submit(_summary)

        profile = profile_f.result()
        foresight = foresight_f.result()
        summary = summary_f.result()

    return {
        "profile": profile,
        "foresight": foresight,
        "summary": summary,
        "facts": [],
    }


def retrieve_for_query(query: str, query_time=None, mode: str = "search") -> dict:
    """Query retrieval with 3 modes:
    - "search": hybrid search top-5 facts + profile + foresight (fastest, least tokens)
    - "summary": hybrid search top-5 + rolling summary + profile + foresight (most tokens, catches search misses)
    - "date": all facts for detected date + profile + foresight (precise for temporal queries)
    """
    t0 = time.time()
    timings = {}

    # Step 1: Temporal parse (regex, ~0ms)
    reference_date = str(query_time) if query_time else None
    temporal_result = parse_temporal_query(query, reference_date)
    timings["temporal_parse_s"] = 0.0

    date_filter = None
    if temporal_result:
        date_filter = {
            "date_from": temporal_result["date_from"],
            "date_to": temporal_result["date_to"],
        }
        print(f"  [query-retrieval] Temporal: {date_filter['date_from']} to {date_filter['date_to']}")

    # Step 2: Mode-specific retrieval
    if mode == "date":
        # Single DB call: profile + foresight + summary + date facts
        t_ctx = time.time()
        ctx = db.get_query_context(query_time, date_filter=date_filter) if query_time else {
            "profile": db.get_user_profile(), "foresight": [], "summary": {}, "date_facts": []
        }
        timings["context_s"] = round(time.time() - t_ctx, 3)

        facts = [
            {"fact_id": f["id"], "fact_text": f["fact_text"],
             "conversation_date": f.get("conversation_date"), "category": f.get("category")}
            for f in ctx.get("date_facts", [])
        ]
        timings["embed_s"] = 0.0
        timings["hybrid_search_s"] = 0.0

    else:
        # "search" or "summary" — embed + single PG call (context + keyword) + Qdrant vector search
        # Step A: Embed query + fetch all PG data in parallel
        def _timed_embed():
            t = time.time()
            result = embed_text(query)
            return result, round(time.time() - t, 3)

        def _timed_context():
            t = time.time()
            result = db.get_query_context(
                query_time, date_filter=date_filter,
                keyword_query=query, keyword_top_k=RETRIEVAL_TOP_K * 2
            ) if query_time else {
                "profile": db.get_user_profile(), "foresight": [], "summary": {}, "date_facts": [], "keyword_results": []
            }
            return result, round(time.time() - t, 3)

        with ThreadPoolExecutor(max_workers=2) as executor:
            embed_future = executor.submit(_timed_embed)
            context_future = executor.submit(_timed_context)

            query_embedding, timings["embed_s"] = embed_future.result()
            ctx, timings["context_s"] = context_future.result()

        # Step B: Qdrant vector search + RRF fusion (needs embedding from step A)
        t_search = time.time()
        facts = hybrid_search_facts(query_embedding, ctx.get("keyword_results", []), RETRIEVAL_TOP_K, date_filter)
        timings["hybrid_search_s"] = round(time.time() - t_search, 3)

    profile = ctx["profile"]
    foresight = ctx["foresight"]
    summary = ctx["summary"] if mode == "summary" else {}

    timings["total_retrieval_s"] = round(time.time() - t0, 3)
    timings["mode"] = mode
    print(f"  [query-retrieval] mode={mode} | {len(facts)} facts | embed: {timings['embed_s']}s | search: {timings['hybrid_search_s']}s | ctx: {timings['context_s']}s | total: {timings['total_retrieval_s']}s")

    return {
        "profile": profile,
        "foresight": foresight,
        "summary": summary,
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
    """Compose context for query: matched facts + conversation history + foresight + profile."""
    parts = []

    # Matched facts first (highest priority for query)
    facts = result.get("facts", [])
    if facts:
        parts.append("=== MATCHED FACTS ===")
        for f in facts:
            date_tag = f" [{f['conversation_date']}]" if f.get("conversation_date") else ""
            parts.append(f"- {f['fact_text']}{date_tag}")
        parts.append("")

    # Rolling summary (fills gaps when search misses)
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

