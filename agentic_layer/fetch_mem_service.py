import time
from datetime import datetime, timezone
from config import RETRIEVAL_TOP_K, SCENE_TOP_N
import db
from agentic_layer.retrieval_utils import (
    hybrid_search, filter_active_foresight, cosine_similarity, deduplicate_facts,
)
from agentic_layer.vectorize_service import embed_text


EPISODE_SIM_THRESHOLD = 0.3
EPISODE_MIN_KEEP = 3
EPISODE_STALENESS_THRESHOLD = 0.5  # drop episodes where >50% of facts are superseded


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


def retrieve(query: str, query_time: datetime = None,
             top_k_facts: int = RETRIEVAL_TOP_K,
             top_n_scenes: int = SCENE_TOP_N) -> dict:
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
    if query_time is None:
        query_time = datetime.now(timezone.utc).replace(tzinfo=None)

    retrieval_start = time.time()

    # Compute query embedding once — reused by vector search, foresight filter, episode filter
    t0 = time.time()
    query_embedding = embed_text(query)
    print(f"  [retrieval] Embed query: {time.time() - t0:.2f}s")

    # Step 1: RRF hybrid search over atomic facts (temporal filtering applied when query_time set)
    t0 = time.time()
    top_facts = hybrid_search(query, top_k_facts, query_time=query_time,
                              query_embedding=query_embedding)
    print(f"  [retrieval] Hybrid search ({len(top_facts)} facts): {time.time() - t0:.2f}s")

    if not top_facts:
        print(f"  [retrieval] No facts found, returning empty. Total: {time.time() - retrieval_start:.2f}s")
        return {"episodes": [], "foresight": [], "profile": None, "facts": [], "scenes": []}

    # Step 1b: Deduplicate near-identical facts (cosine > 0.9)
    before_dedup = len(top_facts)
    top_facts = deduplicate_facts(top_facts)
    if len(top_facts) < before_dedup:
        print(f"  [retrieval] Dedup: {before_dedup} → {len(top_facts)} facts")

    # Step 2: Score MemScenes by best fact score
    t0 = time.time()
    scored_scenes = _score_scenes(top_facts)
    print(f"  [retrieval] Score scenes ({len(scored_scenes)} scenes): {time.time() - t0:.2f}s")

    # Step 3: Select top-N scenes, pool their episodes (filtered by query_time)
    t0 = time.time()
    selected_scene_ids = [s["scene_id"] for s in scored_scenes[:top_n_scenes]]
    all_episodes = _pool_episodes(selected_scene_ids, query_time=query_time)
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

    # Step 6: Foresight filtering (semantic similarity + recency dedup)
    t0 = time.time()
    active_foresight = filter_active_foresight(query_time, query_embedding=query_embedding)
    print(f"  [retrieval] Foresight ({len(active_foresight)} active): {time.time() - t0:.2f}s")

    # Step 7: Get user profile (only for "now" queries — profile is always latest state)
    is_temporal_query = query_time and query_time.date() != datetime.now().date()
    profile = None if is_temporal_query else db.get_user_profile()

    print(f"  [retrieval] Total: {time.time() - retrieval_start:.2f}s "
          f"({len(ranked_episodes)} episodes, {len(top_facts)} facts, {len(active_foresight)} foresight)")

    return {
        "episodes": ranked_episodes,
        "foresight": active_foresight,
        "profile": profile,
        "facts": top_facts[:top_k_facts],
        "scenes": scored_scenes[:top_n_scenes],
    }


def compose_context(retrieval_result: dict) -> str:
    """
    Compose retrieved information into a text context block
    suitable for downstream LLM consumption.
    """
    parts = []

    # User profile
    profile = retrieval_result.get("profile")
    if profile:
        parts.append("=== User Profile ===")
        if profile.explicit_facts:
            parts.append("Known facts: " + "; ".join(profile.explicit_facts))
        if profile.implicit_traits:
            parts.append("Traits: " + "; ".join(profile.implicit_traits))
        parts.append("")

    # Relevant episodes (P1: use conversation_date instead of created_at)
    episodes = retrieval_result.get("episodes", [])
    if episodes:
        parts.append("=== Relevant Memory Episodes ===")
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

    # Active foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        parts.append("=== Active Foresight (time-valid) ===")
        for fs in foresight:
            source = fs["source_date"].strftime("%Y-%m-%d") if hasattr(fs.get("source_date"), 'strftime') else (str(fs["source_date"]) if fs.get("source_date") else "unknown")
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (from: {source}, valid until: {until})")
        parts.append("")

    # Top matching facts (P2: include conversation_date, I2: filter superseded)
    facts = retrieval_result.get("facts", [])
    if facts:
        fact_ids = [f["fact_id"] for f in facts]
        superseded_map = db.get_superseded_map(fact_ids)
        fact_ids_in_context = set(fact_ids)

        # Drop old facts whose replacement is already present
        facts = [
            f for f in facts
            if not (f["fact_id"] in superseded_map and superseded_map[f["fact_id"]] in fact_ids_in_context)
        ]

        parts.append("=== Top Matching Facts ===")
        for f in facts[:5]:
            date_tag = f" [{f['conversation_date']}]" if f.get("conversation_date") else ""
            parts.append(f"- {f['fact_text']}{date_tag} (score: {f['rrf_score']:.4f})")

    return "\n".join(parts)
