from datetime import datetime
from config import RETRIEVAL_TOP_K, SCENE_TOP_N
import db
from agentic_layer.retrieval_utils import hybrid_search, filter_active_foresight


def _score_scenes(facts: list[dict]) -> list[dict]:
    """
    Map facts to their parent MemScenes and score each scene
    by the best (max) RRF score among its facts.

    Returns sorted list of {scene_id, best_score, fact_ids}.
    """
    scene_scores = {}  # scene_id -> {best_score, fact_ids}

    for fact in facts:
        memcell = db.get_memcell_by_id(fact["memcell_id"])
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
    Returns list of {memcell_id, episode_text, scene_id, created_at}.
    """
    episodes = []
    seen = set()
    for sid in scene_ids:
        cells = db.get_memcells_by_scene(sid, query_time=query_time)
        for cell in cells:
            if cell["id"] not in seen:
                seen.add(cell["id"])
                episodes.append({
                    "memcell_id": cell["id"],
                    "episode_text": cell["episode_text"],
                    "scene_id": sid,
                    "created_at": cell["created_at"],
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
    return episodes[:top_k]


def retrieve(query: str, query_time: datetime = None,
             top_k_facts: int = RETRIEVAL_TOP_K,
             top_n_scenes: int = SCENE_TOP_N) -> dict:
    """
    Full MemScene-guided retrieval pipeline.

    Args:
        query: The user's query.
        query_time: Timestamp for foresight filtering (defaults to now).
        top_k_facts: Number of top facts from hybrid search.
        top_n_scenes: Number of top scenes to select.

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
        query_time = datetime.now()

    # Step 1: RRF hybrid search over atomic facts (temporal filtering applied when query_time set)
    top_facts = hybrid_search(query, top_k_facts, query_time=query_time)

    if not top_facts:
        return {"episodes": [], "foresight": [], "profile": None, "facts": [], "scenes": []}

    # Step 2: Score MemScenes by best fact score
    scored_scenes = _score_scenes(top_facts)

    # Step 3: Select top-N scenes, pool their episodes (filtered by query_time)
    selected_scene_ids = [s["scene_id"] for s in scored_scenes[:top_n_scenes]]
    all_episodes = _pool_episodes(selected_scene_ids, query_time=query_time)

    # Step 4: Re-rank episodes by fact scores (no extra embedding calls)
    ranked_episodes = _rerank_episodes(all_episodes, top_facts)

    # Step 5: Foresight filtering
    active_foresight = filter_active_foresight(query_time)

    # Step 6: Get user profile (skip for historical queries — profile is always "current")
    is_historical = query_time and query_time.date() < datetime.now().date()
    profile = None if is_historical else db.get_user_profile()

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

    # Relevant episodes
    episodes = retrieval_result.get("episodes", [])
    if episodes:
        parts.append("=== Relevant Memory Episodes ===")
        for i, ep in enumerate(episodes, 1):
            date_str = ep["created_at"].strftime("%Y-%m-%d") if ep.get("created_at") else "unknown"
            parts.append(f"[{i}] ({date_str}) {ep['episode_text']}")
        parts.append("")

    # Active foresight
    foresight = retrieval_result.get("foresight", [])
    if foresight:
        parts.append("=== Active Foresight (time-valid) ===")
        for fs in foresight:
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            parts.append(f"- {fs['description']} (valid until: {until})")
        parts.append("")

    # Top matching facts
    facts = retrieval_result.get("facts", [])
    if facts:
        parts.append("=== Top Matching Facts ===")
        for f in facts[:5]:
            parts.append(f"- {f['fact_text']} (score: {f['rrf_score']:.4f})")

    return "\n".join(parts)
