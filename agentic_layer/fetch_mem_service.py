import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from config import RETRIEVAL_TOP_K, COHERE_API_KEY
import db
import vector_store
from agentic_layer.retrieval_utils import (
    hybrid_search, filter_active_foresight, deduplicate_facts,
)
from agentic_layer.vectorize_service import embed_text
from agentic_layer.temporal_parser import parse_temporal_query

# Cohere reranker
_cohere_client = None

def _get_cohere_client():
    global _cohere_client
    if _cohere_client is None and COHERE_API_KEY:
        import cohere
        _cohere_client = cohere.Client(api_key=COHERE_API_KEY)
    return _cohere_client


_last_rerank_time = 0

def rerank_facts(query: str, facts: list[dict], top_k: int = 5) -> list[dict]:
    """Rerank facts using Cohere reranker. Rate-limited to 10 req/min. Falls back if unavailable."""
    global _last_rerank_time
    client = _get_cohere_client()
    if not client or not facts:
        return facts[:top_k]

    # Rate limit: 10 req/min = 1 req per 6s
    elapsed = time.time() - _last_rerank_time
    if elapsed < 6:
        time.sleep(6 - elapsed)

    try:
        t0 = time.time()
        _last_rerank_time = time.time()
        documents = [f["fact_text"] for f in facts]
        response = client.rerank(
            query=query,
            documents=documents,
            top_n=top_k,
            model="rerank-v3.5",
        )
        reranked = []
        for result in response.results:
            fact = facts[result.index].copy()
            fact["rerank_score"] = result.relevance_score
            reranked.append(fact)
        print(f"  [retrieval] Rerank: {len(facts)} → {len(reranked)} facts ({time.time() - t0:.2f}s)")
        return reranked
    except Exception as e:
        print(f"  [retrieval] Rerank failed ({e}), using RRF order")
        return facts[:top_k]


FAST_FACTS_LIMIT = 10
FAST_FACT_MIN_SCORE = 0.005
FAST_FORESIGHT_MIN_SIM = 0.7

# Category detection
CATEGORY_SIM_THRESHOLD = 0.25
CATEGORY_TOP_K = 3
_category_cache = None


def _load_category_cache():
    global _category_cache
    rows = db.get_all_category_embeddings()
    _category_cache = []
    for r in rows:
        if r["embedding"]:
            _category_cache.append({
                "category_name": r["category_name"],
                "embedding": np.array(r["embedding"]),
            })


def invalidate_category_cache():
    global _category_cache
    _category_cache = None


def _detect_query_categories(query_embedding: list[float]) -> list[str]:
    """Detect relevant categories by cosine similarity against category embeddings."""
    global _category_cache
    if _category_cache is None:
        _load_category_cache()

    if not _category_cache:
        return []

    query_vec = np.array(query_embedding)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []

    scores = []
    for cat in _category_cache:
        cat_norm = np.linalg.norm(cat["embedding"])
        if cat_norm == 0:
            continue
        similarity = float(np.dot(query_vec, cat["embedding"]) / (query_norm * cat_norm))
        if similarity >= CATEGORY_SIM_THRESHOLD:
            scores.append((cat["category_name"], similarity))

    scores.sort(key=lambda x: -x[1])
    return [name for name, _ in scores[:CATEGORY_TOP_K]]


def _merge_fact_results(list_a: list[dict], list_b: list[dict]) -> list[dict]:
    """Merge two fact lists, deduplicating by fact_id, keeping higher rrf_score."""
    seen = {}
    for f in list_a + list_b:
        fid = f["fact_id"]
        if fid not in seen or f["rrf_score"] > seen[fid]["rrf_score"]:
            seen[fid] = f
    return sorted(seen.values(), key=lambda x: x["rrf_score"], reverse=True)


def retrieve_simple(query: str, query_time: datetime = None) -> dict:
    """Tier-1 retrieval: profile + all category summaries. No search."""
    IST = timezone(timedelta(hours=5, minutes=30))
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)

    retrieval_start = time.time()

    with ThreadPoolExecutor(max_workers=2) as executor:
        profile_future = executor.submit(db.get_user_profile)
        categories_future = executor.submit(db.get_profile_categories)

        profile = profile_future.result()
        category_profiles = categories_future.result()

    context_compose_s = round(time.time() - retrieval_start, 3)
    print(f"  [simple-retrieval] profile + {len(category_profiles)} categories: {context_compose_s}s (parallel)")

    return {
        "episodes": [],
        "foresight": [],
        "profile": profile,
        "facts": [],
        "category_profiles": category_profiles,
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
    """Full retrieval: hybrid search facts → episodes + foresight + categories + profile."""
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

    # Step 1b: Detect relevant categories
    relevant_cats = _detect_query_categories(query_embedding)
    if relevant_cats:
        print(f"  [retrieval] Categories: {relevant_cats}")

    # Step 2: Launch foresight + category + profile + consolidated search in parallel
    is_historical = (date_filter is not None and not is_mixed)
    parallel_executor = ThreadPoolExecutor(max_workers=4)

    def _run_foresight():
        t = time.time()
        result = filter_active_foresight(effective_query_time, query_embedding=query_embedding)
        return result, time.time() - t

    def _run_category_fetch():
        return db.get_profile_categories(relevant_cats) if relevant_cats else []

    def _run_profile():
        if is_historical:
            return None
        return db.get_user_profile()

    def _run_consolidated_search():
        t = time.time()
        cf_results = vector_store.search_consolidated_facts(
            query_embedding, top_k=5, date_filter=date_filter)
        cf_ids = [hit["consolidated_fact_id"] for hit in cf_results]
        cf_rows = db.get_consolidated_facts_by_ids(cf_ids) if cf_ids else []
        print(f"  [retrieval] Consolidated facts: {len(cf_rows)} ({time.time() - t:.2f}s) [parallel]")
        return cf_rows

    foresight_future = parallel_executor.submit(_run_foresight)
    category_future = parallel_executor.submit(_run_category_fetch)
    profile_future = parallel_executor.submit(_run_profile)
    consolidated_future = parallel_executor.submit(_run_consolidated_search)

    # Step 3: Hybrid search (keyword + vector → RRF)
    search_k = top_k_facts
    t0 = time.time()
    if is_mixed:
        historical_facts = hybrid_search(query, search_k,
                                         query_time=effective_query_time,
                                         query_embedding=query_embedding, date_filter=date_filter)
        current_facts = hybrid_search(query, search_k, query_time=query_time,
                                      query_embedding=query_embedding, date_filter=None)
        top_facts = _merge_fact_results(historical_facts, current_facts)
    else:
        top_facts = hybrid_search(query, search_k, query_time=effective_query_time,
                                  query_embedding=query_embedding, date_filter=date_filter)
        if date_filter and len(top_facts) < 3:
            print(f"  [retrieval] Date filter returned {len(top_facts)} facts, retrying without filter")
            top_facts = hybrid_search(query, search_k, query_time=effective_query_time,
                                      query_embedding=query_embedding, date_filter=None)

    step_timing["search_s"] = round(time.time() - t0, 3)
    print(f"  [retrieval] Hybrid search ({len(top_facts)} facts): {step_timing['search_s']}s")

    if not top_facts:
        active_foresight, foresight_duration = foresight_future.result()
        category_profiles = category_future.result()
        profile = profile_future.result()
        consolidated_facts = consolidated_future.result()
        parallel_executor.shutdown(wait=False)
        step_timing["foresight_s"] = round(foresight_duration, 3)
        step_timing["context_compose_s"] = 0
        print(f"  [retrieval] No facts found. Total: {time.time() - retrieval_start:.2f}s")
        return {"episodes": [], "foresight": active_foresight, "profile": profile, "facts": [],
                "category_profiles": category_profiles, "consolidated_facts": consolidated_facts,
                "timing": step_timing}

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

    # Collect parallel results
    active_foresight, foresight_duration = foresight_future.result()
    category_profiles = category_future.result()
    profile = profile_future.result()
    consolidated_facts = consolidated_future.result()
    parallel_executor.shutdown(wait=False)
    step_timing["foresight_s"] = round(foresight_duration, 3)

    # Filter foresight by query similarity
    before_foresight_filter = len(active_foresight)
    active_foresight = [fs for fs in active_foresight if fs.get("query_sim", 0.0) >= FAST_FORESIGHT_MIN_SIM]
    if len(active_foresight) < before_foresight_filter:
        print(f"  [retrieval] Foresight filter: {before_foresight_filter} → {len(active_foresight)}")
    print(f"  [retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")
    if category_profiles:
        print(f"  [retrieval] Category profiles: {[cp['category_name'] for cp in category_profiles]}")

    # Episode enrichment: ranked by best fact RRF score, max 5
    t0 = time.time()
    memcell_scores = {}
    for f in top_facts[:top_k_facts]:
        mid = f.get("memcell_id")
        if mid and (mid not in memcell_scores or f["rrf_score"] > memcell_scores[mid]):
            memcell_scores[mid] = f["rrf_score"]
    ranked_memcell_ids = sorted(memcell_scores.keys(), key=lambda m: -memcell_scores[m])
    memcells_map = db.get_memcells_by_ids(ranked_memcell_ids) if ranked_memcell_ids else {}
    episodes = [memcells_map[mid] for mid in ranked_memcell_ids if mid in memcells_map]
    print(f"  [retrieval] Episodes from {len(ranked_memcell_ids)} memcells: {len(episodes)} ({time.time() - t0:.2f}s)")

    step_timing["context_compose_s"] = round(time.time() - t0, 3)

    total_s = time.time() - retrieval_start
    print(f"  [retrieval] Total: {total_s:.2f}s "
          f"({len(top_facts)} facts, {len(episodes)} episodes, {len(active_foresight)} foresight, {len(category_profiles)} categories)"
          f"{' [temporal]' if date_filter else ''}")

    return {
        "episodes": episodes,
        "foresight": active_foresight,
        "profile": profile,
        "facts": top_facts[:top_k_facts],
        "category_profiles": category_profiles,
        "consolidated_facts": consolidated_facts,
        "timing": step_timing,
    }


def compose_context_fast(retrieval_result: dict) -> str:
    """Compose context from profile + category summaries (fast mode). Hierarchical format."""
    parts = []

    parts.append("=== HIGH-LEVEL CONTEXT ===")

    profile = retrieval_result.get("profile")
    if profile:
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    category_profiles = retrieval_result.get("category_profiles", [])
    if category_profiles:
        for cp in category_profiles:
            parts.append(f"[{cp['category_name']}]")
            parts.append(cp['summary_text'])
        parts.append("")

    return "\n".join(parts)


def compose_context(retrieval_result: dict) -> str:
    """Compose context hierarchically: high-level first, then detailed evidence."""
    parts = []

    # === HIGH-LEVEL CONTEXT ===
    parts.append("=== HIGH-LEVEL CONTEXT ===")

    profile = retrieval_result.get("profile")
    if profile:
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    category_profiles = retrieval_result.get("category_profiles", [])
    if category_profiles:
        for cp in category_profiles:
            parts.append(f"[{cp['category_name']}]")
            parts.append(cp['summary_text'])
        parts.append("")

    # === DETAILED EVIDENCE ===
    parts.append("=== DETAILED EVIDENCE ===")

    # Consolidated facts
    consolidated = retrieval_result.get("consolidated_facts", [])
    if consolidated:
        for i, cf in enumerate(consolidated, 1):
            date_val = cf.get("conversation_date")
            date_str = str(date_val) if date_val else "unknown"
            parts.append(f"[C{i}] ({date_str}) {cf['consolidated_text']}")
        parts.append("")

    # Episodes
    episodes = retrieval_result.get("episodes", [])
    if episodes:
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
        fact_ids = [f["fact_id"] for f in facts]
        superseded_map = db.get_superseded_map(fact_ids)
        fact_ids_in_context = set(fact_ids)

        facts = [
            f for f in facts
            if not (f["fact_id"] in superseded_map and superseded_map[f["fact_id"]] in fact_ids_in_context)
        ]

        for f in facts[:FAST_FACTS_LIMIT]:
            date_tag = f" [{f['conversation_date']}]" if f.get("conversation_date") else ""
            parts.append(f"- {f['fact_text']}{date_tag} (score: {f['rrf_score']:.4f})")
        parts.append("")

    # Foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)
