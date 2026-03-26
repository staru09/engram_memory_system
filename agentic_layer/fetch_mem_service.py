import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from config import RETRIEVAL_TOP_K, SCENE_TOP_N
import db
from agentic_layer.retrieval_utils import (
    hybrid_search, filter_active_foresight, cosine_similarity, deduplicate_facts,
)
from agentic_layer.vectorize_service import embed_text
from agentic_layer.temporal_parser import parse_temporal_query


EPISODE_SIM_THRESHOLD = 0.3
EPISODE_MIN_KEEP = 3
EPISODE_STALENESS_THRESHOLD = 0.5

# Category detection
CATEGORY_SIM_THRESHOLD = 0.25
CATEGORY_TOP_K = 3
_category_cache = None


def _load_category_cache():
    """Load all category embeddings into memory."""
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
    """Call after ingestion to refresh category embeddings."""
    global _category_cache
    _category_cache = None


def _detect_query_categories(query_embedding: list[float],
                              threshold: float = CATEGORY_SIM_THRESHOLD,
                              top_k: int = CATEGORY_TOP_K) -> list[str]:
    """Detect relevant categories by cosine similarity against category embeddings.
    Reuses the query embedding already computed for hybrid search — zero extra cost."""
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
        if similarity >= threshold:
            scores.append((cat["category_name"], similarity))

    scores.sort(key=lambda x: -x[1])
    return [name for name, _ in scores[:top_k]]


def _score_scenes(facts: list[dict]) -> list[dict]:
    """
    Map facts to their parent MemScenes and score each scene
    by the best (max) RRF score among its facts.

    Returns sorted list of {scene_id, best_score, fact_ids}.
    """
    # Batch-fetch all memcells in a single query
    memcell_ids = list({f["memcell_id"] for f in facts if f.get("memcell_id")})
    memcells_map = db.get_memcells_by_ids(memcell_ids)

    scene_scores = {}  # scene_id -> {best_score, fact_ids}

    for fact in facts:
        memcell = memcells_map.get(fact["memcell_id"])
        if not memcell or not memcell.get("scene_id"):
            continue

        scene_id = memcell["scene_id"]
        score = fact["rrf_score"]

        if scene_id not in scene_scores:
            scene_scores[scene_id] = {"scene_id": scene_id, "best_score": 0.0, "fact_ids": []}

        if score > scene_scores[scene_id]["best_score"]:
            scene_scores[scene_id]["best_score"] = score
        scene_scores[scene_id]["fact_ids"].append(fact["fact_id"])

    return sorted(scene_scores.values(), key=lambda x: x["best_score"], reverse=True)


def _pool_episodes(scene_ids: list[int], query_time=None) -> list[dict]:
    """
    Gather all episodes (MemCells) from the selected scenes.
    When query_time is provided, only includes episodes from conversations
    that happened on or before that date.
    Returns list of {memcell_id, episode_text, scene_id, created_at, conversation_date, embedding}.
    """
    # Single batch query for all scenes
    cells = db.get_memcells_by_scenes(scene_ids, query_time=query_time)

    episodes = []
    seen = set()
    for cell in cells:
        if cell["id"] not in seen:
            seen.add(cell["id"])
            episodes.append({
                "memcell_id": cell["id"],
                "episode_text": cell["episode_text"],
                "scene_id": cell["scene_id"],
                "created_at": cell["created_at"],
                "conversation_date": cell.get("conversation_date"),
                "embedding": cell.get("embedding"),
            })
    return episodes


def _rerank_episodes(episodes: list[dict], scored_facts: list[dict],
                     top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    Re-rank episodes by the best fact score among their child facts.
    No embedding calls needed — reuses RRF scores from the fact search.
    """
    if not episodes:
        return []

    # Build memcell_id → best fact score mapping
    cell_scores = {}
    for f in scored_facts:
        mid = f["memcell_id"]
        if mid not in cell_scores or f["rrf_score"] > cell_scores[mid]:
            cell_scores[mid] = f["rrf_score"]

    for ep in episodes:
        ep["relevance_score"] = cell_scores.get(ep["memcell_id"], 0.0)

    episodes.sort(key=lambda x: x["relevance_score"], reverse=True)
    return [ep for ep in episodes[:top_k] if ep["relevance_score"] > 0]


def _filter_episodes_by_similarity(episodes: list[dict],
                                    query_embedding: list[float],
                                    threshold: float = EPISODE_SIM_THRESHOLD,
                                    min_keep: int = EPISODE_MIN_KEEP) -> list[dict]:
    """
    Filter episodes by cosine similarity to query embedding.
    Keeps episodes above threshold, with a floor of min_keep.
    """
    if not episodes or not query_embedding:
        return episodes

    with_emb = [ep for ep in episodes if ep.get("embedding")]
    without_emb = [ep for ep in episodes if not ep.get("embedding")]

    for ep in with_emb:
        ep["semantic_sim"] = cosine_similarity(query_embedding, ep["embedding"])

    with_emb.sort(key=lambda x: x["semantic_sim"], reverse=True)
    filtered = [ep for ep in with_emb if ep["semantic_sim"] >= threshold]

    # Always keep at least min_keep episodes
    if len(filtered) < min_keep:
        filtered = with_emb[:min_keep]

    return filtered + without_emb


def _filter_stale_episodes(episodes: list[dict], min_keep: int = EPISODE_MIN_KEEP) -> list[dict]:
    """
    Filter out episodes where >50% of their facts have been superseded.
    These episodes contain mostly outdated information and add noise.
    """
    if not episodes:
        return episodes

    memcell_ids = [ep["memcell_id"] for ep in episodes]
    staleness_map = db.get_episode_staleness(memcell_ids)

    fresh = []
    stale = []
    for ep in episodes:
        staleness = staleness_map.get(ep["memcell_id"], 0.0)
        ep["staleness"] = staleness
        if staleness < EPISODE_STALENESS_THRESHOLD:
            fresh.append(ep)
        else:
            stale.append(ep)

    # Always keep at least min_keep episodes, preferring fresh ones
    if len(fresh) >= min_keep:
        return fresh
    # Not enough fresh episodes — backfill with least-stale ones
    stale.sort(key=lambda x: x["staleness"])
    needed = min_keep - len(fresh)
    return fresh + stale[:needed]


def _merge_fact_results(list_a: list[dict], list_b: list[dict]) -> list[dict]:
    """Merge two fact lists, deduplicating by fact_id, keeping higher rrf_score."""
    seen = {}
    for f in list_a + list_b:
        fid = f["fact_id"]
        if fid not in seen or f["rrf_score"] > seen[fid]["rrf_score"]:
            seen[fid] = f
    return sorted(seen.values(), key=lambda x: x["rrf_score"], reverse=True)


def retrieve(query: str, query_time: datetime = None,
             top_k_facts: int = RETRIEVAL_TOP_K,
             top_n_scenes: int = SCENE_TOP_N,
             temporal_result: dict = None) -> dict:
    """
    Full MemScene-guided retrieval pipeline.

    Returns:
        {
            "episodes": [...],       # Re-ranked episode texts
            "foresight": [...],      # Active foresight signals
            "profile": {...},        # User profile
            "facts": [...],          # Top matching facts
            "scenes": [...],         # Selected scene info
        }
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)

    # query_time is IST — used for all comparisons (conversation_date, valid_from/valid_until)

    retrieval_start = time.time()

    # Step 0: Temporal expression detection (skip if pre-computed by caller)
    current_ist = datetime.now(IST)
    if temporal_result is None:
        t0 = time.time()
        temporal_result = parse_temporal_query(query, current_ist)
        if temporal_result:
            print(f"  [retrieval] Temporal parse: {temporal_result.get('date_from')} to {temporal_result.get('date_to')} "
                  f"(mixed={temporal_result.get('is_mixed', False)}) {time.time() - t0:.2f}s")
        else:
            print(f"  [retrieval] Temporal parse: none ({time.time() - t0:.2f}s)")
    else:
        print(f"  [retrieval] Temporal parse: reusing pre-computed result")

    date_filter = None
    effective_query_time = query_time  # IST — used for all filters
    is_mixed = False

    if temporal_result:
        date_filter = {
            "date_from": temporal_result["date_from"],
            "date_to": temporal_result["date_to"],
        }
        is_mixed = temporal_result.get("is_mixed", False)
        if not is_mixed:
            # Temporal queries: resolved date is IST (from temporal parser)
            effective_query_time = datetime.strptime(temporal_result["date_to"], "%Y-%m-%d")

    # Compute query embedding 
    
    t0 = time.time()
    query_embedding = embed_text(query)
    print(f"  [retrieval] Embed query: {time.time() - t0:.2f}s")

    # Detect relevant categories (~0ms)
    relevant_cats = _detect_query_categories(query_embedding)
    if relevant_cats:
        print(f"  [retrieval] Categories: {relevant_cats}")

    # Launch foresight + category fetch in parallel — runs alongside hybrid search + scene/episode chain
    parallel_executor = ThreadPoolExecutor(max_workers=2)

    def _run_foresight():
        t = time.time()
        result = filter_active_foresight(effective_query_time, query_embedding=query_embedding)
        return result, time.time() - t

    def _run_category_fetch():
        return db.get_profile_categories(relevant_cats) if relevant_cats else []

    foresight_future = parallel_executor.submit(_run_foresight)
    category_future = parallel_executor.submit(_run_category_fetch)

    # Step 1: RRF hybrid search over atomic facts
    t0 = time.time()
    if is_mixed:
        # Two-pass retrieval for mixed queries (past + present)
        historical_facts = hybrid_search(query, top_k_facts,
                                         query_time=effective_query_time,
                                         query_embedding=query_embedding, date_filter=date_filter)
        current_facts = hybrid_search(query, top_k_facts, query_time=query_time,
                                      query_embedding=query_embedding, date_filter=None)
        top_facts = _merge_fact_results(historical_facts, current_facts)
    else:
        top_facts = hybrid_search(query, top_k_facts, query_time=effective_query_time,
                                  query_embedding=query_embedding, date_filter=date_filter)

        # Fallback: if date-filtered search returns too few results, retry without filter
        if date_filter and len(top_facts) < 3:
            print(f"  [retrieval] Date filter returned {len(top_facts)} facts, retrying without filter")
            top_facts = hybrid_search(query, top_k_facts, query_time=effective_query_time,
                                      query_embedding=query_embedding, date_filter=None)

    print(f"  [retrieval] Hybrid search ({len(top_facts)} facts): {time.time() - t0:.2f}s")

    if not top_facts:
        # Collect parallel results before returning
        active_foresight, foresight_duration = foresight_future.result()
        category_profiles = category_future.result()
        parallel_executor.shutdown(wait=False)
        print(f"  [retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")
        print(f"  [retrieval] No facts found, returning empty. Total: {time.time() - retrieval_start:.2f}s")
        return {"episodes": [], "foresight": active_foresight, "profile": None, "facts": [],
                "scenes": [], "category_profiles": category_profiles}

    # Step 1b: Deduplicate near-identical facts (cosine > 0.9)
    before_dedup = len(top_facts)
    top_facts = deduplicate_facts(top_facts)
    if len(top_facts) < before_dedup:
        print(f"  [retrieval] Dedup: {before_dedup} → {len(top_facts)} facts")

    # Step 2: Score MemScenes by best fact score
    t0 = time.time()
    scored_scenes = _score_scenes(top_facts)
    print(f"  [retrieval] Score scenes ({len(scored_scenes)} scenes): {time.time() - t0:.2f}s")

    # Step 3: Select top-N scenes, pool their episodes
    t0 = time.time()
    selected_scene_ids = [s["scene_id"] for s in scored_scenes[:top_n_scenes]]
    all_episodes = _pool_episodes(selected_scene_ids, query_time=effective_query_time)
    print(f"  [retrieval] Pool episodes ({len(all_episodes)} from {len(selected_scene_ids)} scenes): {time.time() - t0:.2f}s")

    # Step 4: Re-rank episodes by fact scores (no extra embedding calls) + zero-score filter
    ranked_episodes = _rerank_episodes(all_episodes, top_facts)

    # Step 5: Semantic similarity filter on episodes
    before_filter = len(ranked_episodes)
    ranked_episodes = _filter_episodes_by_similarity(ranked_episodes, query_embedding)

    # Step 5b: Filter out stale episodes (>50% superseded facts)
    ranked_episodes = _filter_stale_episodes(ranked_episodes)
    if len(ranked_episodes) < before_filter:
        print(f"  [retrieval] Episode filter: {before_filter} → {len(ranked_episodes)}")

    # Collect foresight + category profiles (were running in parallel with Steps 1-5)
    active_foresight, foresight_duration = foresight_future.result()
    category_profiles = category_future.result()
    parallel_executor.shutdown(wait=False)
    print(f"  [retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")
    if category_profiles:
        print(f"  [retrieval] Category profiles: {[cp['category_name'] for cp in category_profiles]}")

    # Step 7: Get user profile

    is_historical = (date_filter is not None
                     and not is_mixed
                     and effective_query_time.date() != datetime.now(IST).date())
    profile = None if is_historical else db.get_user_profile()

    print(f"  [retrieval] Total: {time.time() - retrieval_start:.2f}s "
          f"({len(ranked_episodes)} episodes, {len(top_facts)} facts, {len(active_foresight)} foresight, {len(category_profiles)} categories)"
          f"{' [temporal]' if date_filter else ''}")

    return {
        "episodes": ranked_episodes,
        "foresight": active_foresight,
        "profile": profile,
        "facts": top_facts[:top_k_facts],
        "scenes": scored_scenes[:top_n_scenes],
        "category_profiles": category_profiles,
    }


FAST_FACTS_LIMIT = 10
FAST_FACT_MIN_SCORE = 0.005  # drop facts below this RRF score (noise)
FAST_FORESIGHT_MIN_SIM = 0.7  # drop foresight below this query similarity


def retrieve_fast(query: str, query_time: datetime = None,
                  top_k_facts: int = FAST_FACTS_LIMIT,
                  temporal_result: dict = None) -> dict:
    """
    Facts-only retrieval pipeline. Skips scene scoring, episode pooling,
    and all episode filters. Returns facts + foresight + profile only.
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)

    retrieval_start = time.time()

    # Step 0: Temporal expression detection
    current_ist = datetime.now(IST)
    if temporal_result is None:
        t0 = time.time()
        temporal_result = parse_temporal_query(query, current_ist)
        if temporal_result:
            print(f"  [fast-retrieval] Temporal parse: {temporal_result.get('date_from')} to {temporal_result.get('date_to')} "
                  f"(mixed={temporal_result.get('is_mixed', False)}) {time.time() - t0:.2f}s")
        else:
            print(f"  [fast-retrieval] Temporal parse: none ({time.time() - t0:.2f}s)")
    else:
        print(f"  [fast-retrieval] Temporal parse: reusing pre-computed result")

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

    # Step 1: Embed query (reused by vector search + foresight)
    t0 = time.time()
    query_embedding = embed_text(query)
    print(f"  [fast-retrieval] Embed query: {time.time() - t0:.2f}s")

    # Step 2: Detect relevant categories (~0ms, in-memory cosine)
    relevant_cats = _detect_query_categories(query_embedding)
    if relevant_cats:
        print(f"  [fast-retrieval] Categories: {relevant_cats}")

    # Step 3: Launch foresight + category fetch in parallel with hybrid search
    parallel_executor = ThreadPoolExecutor(max_workers=2)

    def _run_foresight():
        t = time.time()
        result = filter_active_foresight(effective_query_time, query_embedding=query_embedding)
        return result, time.time() - t

    def _run_category_fetch():
        return db.get_profile_categories(relevant_cats) if relevant_cats else []

    foresight_future = parallel_executor.submit(_run_foresight)
    category_future = parallel_executor.submit(_run_category_fetch)

    # Step 4: Hybrid search (keyword ∥ vector → RRF)
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
            print(f"  [fast-retrieval] Date filter returned {len(top_facts)} facts, retrying without filter")
            top_facts = hybrid_search(query, top_k_facts, query_time=effective_query_time,
                                      query_embedding=query_embedding, date_filter=None)

    print(f"  [fast-retrieval] Hybrid search ({len(top_facts)} facts): {time.time() - t0:.2f}s")

    if not top_facts:
        active_foresight, foresight_duration = foresight_future.result()
        category_profiles = category_future.result()
        parallel_executor.shutdown(wait=False)
        print(f"  [fast-retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")
        print(f"  [fast-retrieval] No facts found. Total: {time.time() - retrieval_start:.2f}s")
        return {"episodes": [], "foresight": active_foresight, "profile": None, "facts": [],
                "scenes": [], "category_profiles": category_profiles}

    # Fact deduplication
    before_dedup = len(top_facts)
    top_facts = deduplicate_facts(top_facts)
    if len(top_facts) < before_dedup:
        print(f"  [fast-retrieval] Dedup: {before_dedup} → {len(top_facts)} facts")

    # Step 5: Drop low-scoring facts (noise) — skip when date_filter is active
    # Date-filtered queries already have precision from the date range, so even low-scoring facts are relevant
    if not date_filter:
        before_score_filter = len(top_facts)
        top_facts = [f for f in top_facts if f["rrf_score"] >= FAST_FACT_MIN_SCORE]
        if len(top_facts) < before_score_filter:
            print(f"  [fast-retrieval] Score filter: {before_score_filter} → {len(top_facts)} facts (min score: {FAST_FACT_MIN_SCORE})")

    # Collect foresight + category profiles (were running in parallel)
    active_foresight, foresight_duration = foresight_future.result()
    category_profiles = category_future.result()
    parallel_executor.shutdown(wait=False)

    # Filter out irrelevant foresight by query similarity
    before_foresight_filter = len(active_foresight)
    active_foresight = [fs for fs in active_foresight if fs.get("query_sim", 0.0) >= FAST_FORESIGHT_MIN_SIM]
    if len(active_foresight) < before_foresight_filter:
        print(f"  [fast-retrieval] Foresight relevance filter: {before_foresight_filter} → {len(active_foresight)}")
    print(f"  [fast-retrieval] Foresight ({len(active_foresight)} active): {foresight_duration:.2f}s [parallel]")
    if category_profiles:
        print(f"  [fast-retrieval] Category profiles: {[cp['category_name'] for cp in category_profiles]}")

    # Profile
    is_historical = (date_filter is not None
                     and not is_mixed
                     and effective_query_time.date() != datetime.now(IST).date())
    profile = None if is_historical else db.get_user_profile()

    print(f"  [fast-retrieval] Total: {time.time() - retrieval_start:.2f}s "
          f"({len(top_facts)} facts, {len(active_foresight)} foresight, {len(category_profiles)} categories)"
          f"{' [temporal]' if date_filter else ''}")

    return {
        "episodes": [],
        "foresight": active_foresight,
        "profile": profile,
        "facts": top_facts[:top_k_facts],
        "scenes": [],
        "category_profiles": category_profiles,
    }


def compose_context_fast(retrieval_result: dict) -> str:
    """
    Compose context from facts + foresight + profile + category profiles (no episodes).
    Hierarchical: high-level context first, then detailed evidence.
    """
    parts = []

    # === HIGH-LEVEL CONTEXT ===
    parts.append("=== HIGH-LEVEL CONTEXT ===")

    # User profile (general overview — always included)
    profile = retrieval_result.get("profile")
    if profile:
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    # Category profile sections (relevant categories only)
    category_profiles = retrieval_result.get("category_profiles", [])
    if category_profiles:
        for cp in category_profiles:
            parts.append(f"[{cp['category_name']}]")
            parts.append(cp['summary_text'])
        parts.append("")

    # === DETAILED EVIDENCE ===
    parts.append("=== DETAILED EVIDENCE ===")

    # Top matching facts
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

    # Active foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)


def compose_context(retrieval_result: dict) -> str:
    """
    Compose retrieved information into a text context block.
    Hierarchical: high-level context first, then detailed evidence.
    """
    parts = []

    # === HIGH-LEVEL CONTEXT ===
    parts.append("=== HIGH-LEVEL CONTEXT ===")

    # User profile (general overview)
    profile = retrieval_result.get("profile")
    if profile:
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    # Category profile sections (relevant categories only)
    category_profiles = retrieval_result.get("category_profiles", [])
    if category_profiles:
        for cp in category_profiles:
            parts.append(f"[{cp['category_name']}]")
            parts.append(cp['summary_text'])
        parts.append("")

    # === DETAILED EVIDENCE ===
    parts.append("=== DETAILED EVIDENCE ===")

    # Relevant episodes
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

    # Top matching facts
    facts = retrieval_result.get("facts", [])
    if facts:
        fact_ids = [f["fact_id"] for f in facts]
        superseded_map = db.get_superseded_map(fact_ids)
        fact_ids_in_context = set(fact_ids)

        facts = [
            f for f in facts
            if not (f["fact_id"] in superseded_map and superseded_map[f["fact_id"]] in fact_ids_in_context)
        ]

        for f in facts[:5]:
            date_tag = f" [{f['conversation_date']}]" if f.get("conversation_date") else ""
            parts.append(f"- {f['fact_text']}{date_tag} (score: {f['rrf_score']:.4f})")
        parts.append("")

    # Active foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")

    return "\n".join(parts)
