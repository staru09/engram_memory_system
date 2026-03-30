import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from config import RETRIEVAL_TOP_K
import db
from agentic_layer.retrieval_utils import (
    hybrid_search, filter_active_foresight, deduplicate_facts,
)
from agentic_layer.vectorize_service import embed_text
from agentic_layer.temporal_parser import parse_temporal_query


FAST_FACTS_LIMIT = 10
FAST_FACT_MIN_SCORE = 0.005
FAST_FORESIGHT_MIN_SIM = 0.7


def _merge_fact_results(list_a: list[dict], list_b: list[dict]) -> list[dict]:
    """Merge two fact lists, deduplicating by fact_id, keeping higher rrf_score."""
    seen = {}
    for f in list_a + list_b:
        fid = f["fact_id"]
        if fid not in seen or f["rrf_score"] > seen[fid]["rrf_score"]:
            seen[fid] = f
    return sorted(seen.values(), key=lambda x: x["rrf_score"], reverse=True)


def retrieve_simple(query: str, query_time: datetime = None) -> dict:
    """Tier-1 retrieval: user profile only. No search."""
    IST = timezone(timedelta(hours=5, minutes=30))
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)

    retrieval_start = time.time()

    t0 = time.time()
    profile = db.get_user_profile()
    profile_time = time.time() - t0

    context_compose_s = round(time.time() - retrieval_start, 3)
    print(f"  [simple-retrieval] profile: {profile_time:.3f}s, total: {context_compose_s}s")

    return {
        "episodes": [],
        "foresight": [],
        "profile": profile,
        "facts": [],
        "timing": {
            "embedding_s": 0,
            "search_s": 0,
            "foresight_s": 0,
            "context_compose_s": context_compose_s,
        },
    }


def retrieve_fast(query: str, query_time: datetime = None,
                  top_k_facts: int = FAST_FACTS_LIMIT,
                  temporal_result: dict = None) -> dict:
    """
    Full retrieval: hybrid search facts → episodes + foresight + profile.
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

    # Step 3: Hybrid search (keyword + vector → RRF)
    t0 = time.time()
    if is_mixed:
        historical_facts = hybrid_search(query, top_k_facts,
                                         query_time=effective_query_time,
                                         query_embedding=query_embedding, date_filter=date_filter)
        current_facts = hybrid_search(query, top_k_facts, query_time=query_time,
                                      query_embedding=query_embedding, date_filter=None)
        top_facts = _merge_fact_results(historical_facts, current_facts)
    else:
        top_facts = hybrid_search(query, top_k_facts, query_time=effective_query_time,
                                  query_embedding=query_embedding, date_filter=date_filter)
        if date_filter and len(top_facts) < 3:
            print(f"  [retrieval] Date filter returned {len(top_facts)} facts, retrying without filter")
            top_facts = hybrid_search(query, top_k_facts, query_time=effective_query_time,
                                      query_embedding=query_embedding, date_filter=None)

    step_timing["search_s"] = round(time.time() - t0, 3)
    print(f"  [retrieval] Hybrid search ({len(top_facts)} facts): {step_timing['search_s']}s")

    if not top_facts:
        active_foresight, foresight_duration = foresight_future.result()
        parallel_executor.shutdown(wait=False)
        step_timing["foresight_s"] = round(foresight_duration, 3)
        step_timing["context_compose_s"] = 0
        print(f"  [retrieval] No facts found. Total: {time.time() - retrieval_start:.2f}s")
        return {"episodes": [], "foresight": active_foresight, "profile": None,
                "facts": [], "timing": step_timing}

    # Fact deduplication
    before_dedup = len(top_facts)
    top_facts = deduplicate_facts(top_facts)
    if len(top_facts) < before_dedup:
        print(f"  [retrieval] Dedup: {before_dedup} → {len(top_facts)} facts")

    # Drop low-scoring facts — skip when date_filter is active
    if not date_filter:
        before_score_filter = len(top_facts)
        top_facts = [f for f in top_facts if f["rrf_score"] >= FAST_FACT_MIN_SCORE]
        if len(top_facts) < before_score_filter:
            print(f"  [retrieval] Score filter: {before_score_filter} → {len(top_facts)} facts")

    # Collect foresight
    active_foresight, foresight_duration = foresight_future.result()
    parallel_executor.shutdown(wait=False)
    step_timing["foresight_s"] = round(foresight_duration, 3)

    # Filter foresight by query similarity
    before_foresight_filter = len(active_foresight)
    active_foresight = [fs for fs in active_foresight if fs.get("query_sim", 0.0) >= FAST_FORESIGHT_MIN_SIM]
    if len(active_foresight) < before_foresight_filter:
        print(f"  [retrieval] Foresight filter: {before_foresight_filter} → {len(active_foresight)}")
    print(f"  [retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")

    # Episode enrichment: fetch episodes from top fact memcell IDs
    t0 = time.time()
    memcell_ids = list(set(f["memcell_id"] for f in top_facts[:top_k_facts] if f.get("memcell_id")))[:5]
    memcells_map = db.get_memcells_by_ids(memcell_ids) if memcell_ids else {}
    episodes = list(memcells_map.values())
    print(f"  [retrieval] Episodes from {len(memcell_ids)} memcells: {len(episodes)} ({time.time() - t0:.2f}s)")

    # Profile
    is_historical = (date_filter is not None
                     and not is_mixed
                     and effective_query_time.date() != datetime.now(IST).date())
    profile = None if is_historical else db.get_user_profile()

    step_timing["context_compose_s"] = round(time.time() - t0, 3)

    total_s = time.time() - retrieval_start
    print(f"  [retrieval] Total: {total_s:.2f}s "
          f"({len(top_facts)} facts, {len(episodes)} episodes, {len(active_foresight)} foresight)"
          f"{' [temporal]' if date_filter else ''}")

    return {
        "episodes": episodes,
        "foresight": active_foresight,
        "profile": profile,
        "facts": top_facts[:top_k_facts],
        "timing": step_timing,
    }


def compose_context_fast(retrieval_result: dict) -> str:
    """Compose context from profile only (fast mode)."""
    parts = []

    profile = retrieval_result.get("profile")
    if profile:
        parts.append("=== USER PROFILE ===")
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    foresight = retrieval_result.get("foresight", [])
    if foresight:
        parts.append("=== ACTIVE FORESIGHT ===")
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)


def compose_context(retrieval_result: dict) -> str:
    """Compose context from facts + episodes + foresight + profile (normal mode)."""
    parts = []

    # User profile
    profile = retrieval_result.get("profile")
    if profile:
        parts.append("=== USER PROFILE ===")
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    # Episodes
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

    # Facts
    facts = retrieval_result.get("facts", [])
    if facts:
        parts.append("=== FACTS ===")
        fact_ids = [f["fact_id"] for f in facts]
        superseded_map = db.get_superseded_map(fact_ids)
        fact_ids_in_context = set(fact_ids)

        facts = [
            f for f in facts
            if not (f["fact_id"] in superseded_map and superseded_map[f["fact_id"]] in fact_ids_in_context)
        ]

        for f in facts[:FAST_FACTS_LIMIT]:
            date_tag = f" [{f['conversation_date']}]" if f.get("conversation_date") else ""
            parts.append(f"- {f['fact_text']}{date_tag}")
        parts.append("")

    # Foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        parts.append("=== ACTIVE FORESIGHT ===")
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)
