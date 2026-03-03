from datetime import datetime, date
from config import RRF_K, RETRIEVAL_TOP_K
import db
import vector_store
from agentic_layer.vectorize_service import embed_text


def keyword_search(query: str, top_k: int = RETRIEVAL_TOP_K, query_time=None) -> list[dict]:
    """
    PostgreSQL full-text search on atomic_facts using ts_rank.
    Returns list of {fact_id, memcell_id, fact_text, rank}.
    """
    return db.keyword_search_facts(query, top_k, query_time=query_time)


def vector_search(query: str, top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    Qdrant cosine similarity search on atomic fact embeddings.
    Returns list of {fact_id, memcell_id, score}.
    """
    query_embedding = embed_text(query)
    return vector_store.search_facts(query_embedding, top_k)


def rrf_fusion(keyword_results: list[dict], vector_results: list[dict],
               k: int = RRF_K) -> list[dict]:
    """
    Reciprocal Rank Fusion: merge two ranked lists.
    score(d) = Σ 1/(k + rank) for each list where d appears.

    Returns merged list sorted by RRF score descending.
    """
    scores = {}  # fact_id -> {score, memcell_id, fact_text}

    # Score from keyword results
    for rank, result in enumerate(keyword_results, start=1):
        fid = result["id"]
        if fid not in scores:
            scores[fid] = {"fact_id": fid, "memcell_id": result["memcell_id"],
                           "fact_text": result["fact_text"], "rrf_score": 0.0}
        scores[fid]["rrf_score"] += 1.0 / (k + rank)

    # Score from vector results
    for rank, result in enumerate(vector_results, start=1):
        fid = result["fact_id"]
        if fid not in scores:
            # Need to fetch fact_text from DB
            fact = db.get_fact_by_id(fid)
            fact_text = fact["fact_text"] if fact else ""
            scores[fid] = {"fact_id": fid, "memcell_id": result["memcell_id"],
                           "fact_text": fact_text, "rrf_score": 0.0}
        scores[fid]["rrf_score"] += 1.0 / (k + rank)

    # Sort by RRF score descending
    merged = sorted(scores.values(), key=lambda x: x["rrf_score"], reverse=True)
    return merged


def _temporal_post_filter(facts: list[dict], query_time) -> list[dict]:
    """Post-filter fused results by temporal bounds from DB.

    Removes facts that didn't exist yet at query_time or were already superseded.
    """
    query_date = query_time.date() if isinstance(query_time, datetime) else query_time
    filtered = []
    for f in facts:
        fact_row = db.get_fact_by_id(f["fact_id"])
        if not fact_row:
            continue
        conv_date = fact_row.get("conversation_date")
        if conv_date and conv_date > query_date:
            continue  # Fact didn't exist yet at query time
        sup_date = fact_row.get("superseded_on")
        if sup_date and sup_date <= query_date:
            continue  # Fact was already superseded by query time
        filtered.append(f)
    return filtered


TEMPORAL_OVERFETCH_MULTIPLIER = 3


def hybrid_search(query: str, top_k: int = RETRIEVAL_TOP_K, query_time=None) -> list[dict]:
    """
    Run both keyword + vector search and fuse via RRF.
    When query_time is provided, over-fetches then post-filters to compensate
    for temporal filtering removing candidates.
    """
    if query_time:
        fetch_k = top_k * TEMPORAL_OVERFETCH_MULTIPLIER
        kw_results = keyword_search(query, fetch_k, query_time=query_time)
        vec_results = vector_search(query, fetch_k)
        fused = rrf_fusion(kw_results, vec_results)
        fused = _temporal_post_filter(fused, query_time)
        return fused[:top_k]
    else:
        kw_results = keyword_search(query, top_k)
        vec_results = vector_search(query, top_k)
        return rrf_fusion(kw_results, vec_results)


def filter_active_foresight(query_time: datetime = None) -> list[dict]:
    """
    Return foresight signals valid at the given time.
    Filters out expired foresight.
    """
    if query_time is None:
        query_time = datetime.now()
    return db.get_active_foresight(query_time)
