import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from config import RETRIEVAL_TOP_K
import db
import vector_store
from agentic_layer.retrieval_utils import filter_active_foresight
from agentic_layer.vectorize_service import embed_text
from agentic_layer.temporal_parser import parse_temporal_query


FAST_FORESIGHT_MIN_SIM = 0.7
CONSOLIDATED_TOP_K = 10


def retrieve_simple(query: str, query_time: datetime = None) -> dict:
    """Tier-1 retrieval: user profile + recent consolidated facts. No search."""
    IST = timezone(timedelta(hours=5, minutes=30))
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)

    retrieval_start = time.time()

    t0 = time.time()
    recent_cf = db.get_recent_consolidated_facts(limit=10)
    cf_time = time.time() - t0

    t0 = time.time()
    profile = db.get_user_profile()
    profile_time = time.time() - t0

    context_compose_s = round(time.time() - retrieval_start, 3)
    print(f"  [simple-retrieval] consolidated: {cf_time:.3f}s ({len(recent_cf)}), profile: {profile_time:.3f}s, total: {context_compose_s}s")

    return {
        "episodes": [],
        "foresight": [],
        "profile": profile,
        "facts": [],
        "consolidated_facts": recent_cf,
        "timing": {
            "embedding_s": 0,
            "search_s": 0,
            "foresight_s": 0,
            "context_compose_s": context_compose_s,
        },
    }


def _hybrid_search_consolidated(query: str, query_embedding: list[float],
                                 top_k: int = CONSOLIDATED_TOP_K,
                                 date_filter: dict = None) -> list[dict]:
    """Hybrid search over consolidated facts: vector + keyword → RRF merge."""
    from config import RRF_K, RRF_KEYWORD_WEIGHT, RRF_VECTOR_WEIGHT

    # Vector search
    vector_results = vector_store.search_consolidated_facts(
        query_embedding, top_k=top_k * 2, date_filter=date_filter)

    # Keyword search
    keyword_results = db.keyword_search_consolidated(query, top_k=top_k * 2, date_filter=date_filter)

    # RRF fusion
    scores = {}
    for rank, hit in enumerate(vector_results):
        cf_id = hit["consolidated_fact_id"]
        scores[cf_id] = scores.get(cf_id, 0) + RRF_VECTOR_WEIGHT / (RRF_K + rank + 1)

    for rank, hit in enumerate(keyword_results):
        cf_id = hit["id"]
        scores[cf_id] = scores.get(cf_id, 0) + RRF_KEYWORD_WEIGHT / (RRF_K + rank + 1)

    if not scores:
        return []

    # Fetch full consolidated facts from DB
    cf_ids = list(scores.keys())
    cf_rows = db.get_consolidated_facts_by_ids(cf_ids)
    cf_map = {r["id"]: r for r in cf_rows}

    results = []
    for cf_id, rrf_score in sorted(scores.items(), key=lambda x: -x[1]):
        if cf_id in cf_map:
            row = cf_map[cf_id]
            results.append({
                "consolidated_fact_id": cf_id,
                "consolidated_text": row["consolidated_text"],
                "fact_ids": row["fact_ids"],
                "metadata": row["metadata"],
                "conversation_date": row["conversation_date"],
                "rrf_score": rrf_score,
            })

    return results[:top_k]


def retrieve_fast(query: str, query_time: datetime = None,
                  top_k: int = CONSOLIDATED_TOP_K,
                  temporal_result: dict = None) -> dict:
    """
    Tier-2 retrieval: hybrid search consolidated facts → episodes + foresight + profile.
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)

    retrieval_start = time.time()
    step_timing = {}

    # Step 0: Temporal expression detection
    current_ist = datetime.now(IST)
    if temporal_result is None:
        t0 = time.time()
        temporal_result = parse_temporal_query(query, current_ist)
        if temporal_result:
            print(f"  [retrieval] Temporal parse: {temporal_result.get('date_from')} to {temporal_result.get('date_to')} "
                  f"(mixed={temporal_result.get('is_mixed', False)}) {time.time() - t0:.2f}s")
        else:
            print(f"  [retrieval] Temporal parse: none ({time.time() - t0:.2f}s)")

    date_filter = None
    effective_query_time = query_time
    is_mixed = False

    if temporal_result:
        date_filter = {
            "date_from": temporal_result["date_from"],
            "date_to": temporal_result["date_to"],
        }
        is_mixed = temporal_result.get("is_mixed", False)
        if not is_mixed:
            effective_query_time = datetime.strptime(temporal_result["date_to"], "%Y-%m-%d")

    # Step 1: Embed query
    t0 = time.time()
    query_embedding = embed_text(query)
    step_timing["embedding_s"] = round(time.time() - t0, 3)
    print(f"  [retrieval] Embed query: {step_timing['embedding_s']}s")

    # Step 2: Launch foresight in parallel with search
    parallel_executor = ThreadPoolExecutor(max_workers=1)

    def _run_foresight():
        t = time.time()
        result = filter_active_foresight(effective_query_time, query_embedding=query_embedding)
        return result, time.time() - t

    foresight_future = parallel_executor.submit(_run_foresight)

    # Step 3: Hybrid search over consolidated facts
    t0 = time.time()
    if is_mixed:
        historical = _hybrid_search_consolidated(query, query_embedding, top_k,
                                                  date_filter=date_filter)
        current = _hybrid_search_consolidated(query, query_embedding, top_k,
                                               date_filter=None)
        # Merge, dedup by consolidated_fact_id
        seen = {}
        for cf in historical + current:
            cid = cf["consolidated_fact_id"]
            if cid not in seen or cf["rrf_score"] > seen[cid]["rrf_score"]:
                seen[cid] = cf
        top_consolidated = sorted(seen.values(), key=lambda x: -x["rrf_score"])[:top_k]
    else:
        top_consolidated = _hybrid_search_consolidated(query, query_embedding, top_k,
                                                        date_filter=date_filter)
        if date_filter and len(top_consolidated) < 3:
            print(f"  [retrieval] Date filter returned {len(top_consolidated)}, retrying without filter")
            top_consolidated = _hybrid_search_consolidated(query, query_embedding, top_k,
                                                            date_filter=None)

    step_timing["search_s"] = round(time.time() - t0, 3)
    print(f"  [retrieval] Hybrid search ({len(top_consolidated)} consolidated facts): {step_timing['search_s']}s")

    if not top_consolidated:
        active_foresight, foresight_duration = foresight_future.result()
        parallel_executor.shutdown(wait=False)
        step_timing["foresight_s"] = round(foresight_duration, 3)
        step_timing["context_compose_s"] = 0
        print(f"  [retrieval] No consolidated facts found. Total: {time.time() - retrieval_start:.2f}s")
        return {"episodes": [], "foresight": active_foresight, "profile": None,
                "facts": [], "consolidated_facts": [], "timing": step_timing}

    # Step 4: Get episodes from linked atomic facts → memcell IDs
    t0 = time.time()
    all_fact_ids = []
    for cf in top_consolidated:
        all_fact_ids.extend(cf.get("fact_ids", []))
    all_fact_ids = list(set(all_fact_ids))

    # Get memcell_ids from atomic facts
    memcell_ids = []
    if all_fact_ids:
        facts_map = db.get_facts_by_ids(all_fact_ids)
        memcell_ids = list(set(f["memcell_id"] for f in facts_map.values() if f.get("memcell_id")))

    memcells_map = db.get_memcells_by_ids(memcell_ids) if memcell_ids else {}
    episodes = list(memcells_map.values())
    print(f"  [retrieval] Episodes from {len(memcell_ids)} memcells: {len(episodes)} ({time.time() - t0:.2f}s)")

    # Collect foresight
    active_foresight, foresight_duration = foresight_future.result()
    parallel_executor.shutdown(wait=False)
    step_timing["foresight_s"] = round(foresight_duration, 3)

    # Filter foresight by query similarity
    active_foresight = [fs for fs in active_foresight if fs.get("query_sim", 0.0) >= FAST_FORESIGHT_MIN_SIM]
    print(f"  [retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")

    # Profile
    is_historical = (date_filter is not None
                     and not is_mixed
                     and effective_query_time.date() != datetime.now(IST).date())
    profile = None if is_historical else db.get_user_profile()

    step_timing["context_compose_s"] = round(time.time() - t0, 3)

    total_s = time.time() - retrieval_start
    print(f"  [retrieval] Total: {total_s:.2f}s "
          f"({len(top_consolidated)} consolidated, {len(episodes)} episodes, {len(active_foresight)} foresight)"
          f"{' [temporal]' if date_filter else ''}")

    return {
        "episodes": episodes,
        "foresight": active_foresight,
        "profile": profile,
        "facts": [],
        "consolidated_facts": top_consolidated,
        "timing": step_timing,
    }


def compose_context_fast(retrieval_result: dict) -> str:
    """Compose context from consolidated facts + profile (fast mode, no search)."""
    parts = []

    # === USER PROFILE ===
    profile = retrieval_result.get("profile")
    if profile:
        parts.append("=== USER PROFILE ===")
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    # === RECENT MEMORIES ===
    consolidated = retrieval_result.get("consolidated_facts", [])
    if consolidated:
        parts.append("=== RECENT MEMORIES ===")
        for i, cf in enumerate(consolidated, 1):
            date_val = cf.get("conversation_date")
            date_str = str(date_val) if date_val else "unknown"
            parts.append(f"[{i}] ({date_str}) {cf['consolidated_text']}")
        parts.append("")

    # Active foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        parts.append("=== ACTIVE FORESIGHT ===")
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)


def compose_context(retrieval_result: dict) -> str:
    """Compose context from consolidated facts + episodes + foresight + profile (normal mode)."""
    parts = []

    # === USER PROFILE ===
    profile = retrieval_result.get("profile")
    if profile:
        parts.append("=== USER PROFILE ===")
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    # === RELEVANT MEMORIES ===
    consolidated = retrieval_result.get("consolidated_facts", [])
    if consolidated:
        parts.append("=== RELEVANT MEMORIES ===")
        for i, cf in enumerate(consolidated, 1):
            date_val = cf.get("conversation_date")
            date_str = str(date_val) if date_val else "unknown"
            parts.append(f"[{i}] ({date_str}) {cf['consolidated_text']}")
        parts.append("")

    # === EPISODES ===
    episodes = retrieval_result.get("episodes", [])
    if episodes:
        parts.append("=== EPISODES ===")
        for i, ep in enumerate(episodes, 1):
            date_val = ep.get("conversation_date")
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime("%Y-%m-%d")
            elif date_val:
                date_str = str(date_val)
            elif ep.get("created_at"):
                date_str = ep["created_at"].strftime("%Y-%m-%d")
            else:
                date_str = "unknown"
            parts.append(f"[{i}] ({date_str}) {ep['episode_text']}")
        parts.append("")

    # === ACTIVE FORESIGHT ===
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        parts.append("=== ACTIVE FORESIGHT ===")
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)
