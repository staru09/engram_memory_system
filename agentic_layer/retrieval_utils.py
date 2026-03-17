import time as _time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone
from config import RRF_K, RETRIEVAL_TOP_K, RRF_KEYWORD_WEIGHT, RRF_VECTOR_WEIGHT, FACT_DEDUP_THRESHOLD
import db
import vector_store
from agentic_layer.vectorize_service import embed_text


FORESIGHT_MAX_RESULTS = 5
FORESIGHT_CACHE_TTL = 60 

_foresight_cache = {"data": None, "ts": 0}


def _get_foresight_cached(query_time_utc, query_time_ist=None) -> list[dict]:
    """Return foresight with in-memory caching (60s TTL)."""
    now = _time.time()
    if _foresight_cache["data"] is not None and now - _foresight_cache["ts"] < FORESIGHT_CACHE_TTL:
        return _foresight_cache["data"]
    data = db.get_active_foresight(query_time_utc, query_time_ist=query_time_ist)
    _foresight_cache["data"] = data
    _foresight_cache["ts"] = now
    return data


def invalidate_foresight_cache():
    """Call after ingestion to force fresh foresight on next query."""
    _foresight_cache["data"] = None
    _foresight_cache["ts"] = 0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    if not a or not b:
        return 0.0
    a_arr, b_arr = np.array(a), np.array(b)
    norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if norm == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / norm)


def keyword_search(query: str, top_k: int = RETRIEVAL_TOP_K, query_time=None,
                    date_filter: dict = None) -> list[dict]:
    """
    PostgreSQL full-text search on atomic_facts using ts_rank.
    Returns list of {fact_id, memcell_id, fact_text, conversation_date, rank}.
    """
    return db.keyword_search_facts(query, top_k, query_time=query_time, date_filter=date_filter)


def vector_search(query_embedding: list[float], top_k: int = RETRIEVAL_TOP_K,
                   date_filter: dict = None) -> list[dict]:
    """
    Qdrant cosine similarity search using a pre-computed query embedding.
    Returns list of {fact_id, memcell_id, score}.
    """
    return vector_store.search_facts(query_embedding, top_k, date_filter=date_filter)


def rrf_fusion(keyword_results: list[dict], vector_results: list[dict],
               k: int = RRF_K) -> list[dict]:
    """
    Reciprocal Rank Fusion: merge two ranked lists with weighted contributions.
    Keyword matches get RRF_KEYWORD_WEIGHT (default 1.5×), vector matches get
    RRF_VECTOR_WEIGHT (default 1.0×).
    Carries conversation_date through for downstream display.
    """
    scores = {}  # fact_id -> {score, memcell_id, fact_text, conversation_date}

    # Score from keyword results (boosted weight)
    kw_ids = set()
    for rank, result in enumerate(keyword_results, start=1):
        fid = result["id"]
        kw_ids.add(fid)
        if fid not in scores:
            scores[fid] = {"fact_id": fid, "memcell_id": result["memcell_id"],
                           "fact_text": result["fact_text"], "rrf_score": 0.0,
                           "conversation_date": result.get("conversation_date")}
        scores[fid]["rrf_score"] += RRF_KEYWORD_WEIGHT / (k + rank)

    # Batch-fetch any vector-only facts (not already in keyword results)
    vec_only_ids = [r["fact_id"] for r in vector_results if r["fact_id"] not in kw_ids]
    vec_facts_map = db.get_facts_by_ids(vec_only_ids) if vec_only_ids else {}

    # Score from vector results (baseline weight)
    for rank, result in enumerate(vector_results, start=1):
        fid = result["fact_id"]
        if fid not in scores:
            fact = vec_facts_map.get(fid)
            fact_text = fact["fact_text"] if fact else ""
            conv_date = fact.get("conversation_date") if fact else None
            scores[fid] = {"fact_id": fid, "memcell_id": result["memcell_id"],
                           "fact_text": fact_text, "rrf_score": 0.0,
                           "conversation_date": conv_date}
        scores[fid]["rrf_score"] += RRF_VECTOR_WEIGHT / (k + rank)

    # Sort by RRF score descending
    merged = sorted(scores.values(), key=lambda x: x["rrf_score"], reverse=True)
    return merged


def hybrid_search(query: str, top_k: int = RETRIEVAL_TOP_K,
                  query_time=None, query_embedding=None,
                  date_filter: dict = None) -> list[dict]:
    """
    Run both keyword + vector search and fuse via RRF.
    Accepts optional pre-computed query_embedding to avoid redundant embed calls.
    Optional date_filter {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"} for temporal queries.
    """
    if query_embedding is None:
        query_embedding = embed_text(query)

    # Run keyword + vector search in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        kw_future = executor.submit(keyword_search, query, top_k, query_time, date_filter)
        vec_future = executor.submit(vector_search, query_embedding, top_k, date_filter)
        kw_results = kw_future.result()
        vec_results = vec_future.result()

    fused = rrf_fusion(kw_results, vec_results)

    if query_time:
        valid_ids = db.filter_facts_by_time(
            [f["fact_id"] for f in fused], query_time
        )
        fused = [f for f in fused if f["fact_id"] in valid_ids]

    return fused[:top_k]


def deduplicate_facts(facts: list[dict], threshold: float = FACT_DEDUP_THRESHOLD) -> list[dict]:
    """
    Remove near-duplicate facts by pairwise cosine similarity.
    Facts are already sorted by rrf_score descending — keeps highest-scored version.
    """
    if not facts:
        return facts

    fact_ids = [f["fact_id"] for f in facts]
    embedding_map = vector_store.get_fact_embeddings(fact_ids)

    selected = []
    for fact in facts:
        emb = embedding_map.get(fact["fact_id"])
        if not emb:
            selected.append(fact)
            continue
        fact["_embedding"] = emb
        is_dup = any(
            cosine_similarity(emb, s["_embedding"]) > threshold
            for s in selected if s.get("_embedding")
        )
        if not is_dup:
            selected.append(fact)

    # Clean up temporary embedding data
    for f in selected:
        f.pop("_embedding", None)

    return selected


def filter_active_foresight(query_time_utc: datetime = None,
                            query_embedding: list[float] = None,
                            query_time_ist: datetime = None) -> list[dict]:
    """
    Return foresight signals valid at query_time, ranked by semantic similarity
    to the query and deduplicated.

    Args:
        query_time_utc: UTC datetime for valid_from/valid_until comparisons
        query_time_ist: IST datetime for conversation_date comparison
        query_embedding: Pre-computed query embedding for relevance scoring
    """
    if query_time_utc is None:
        query_time_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    all_foresight = _get_foresight_cached(query_time_utc, query_time_ist=query_time_ist)

    if not query_embedding or not all_foresight:
        return all_foresight

    # Only consider foresight with stored embeddings
    with_emb = [fs for fs in all_foresight if fs.get("embedding")]
    without_emb = [fs for fs in all_foresight if not fs.get("embedding")]

    # Score each foresight by cosine similarity to query
    for fs in with_emb:
        fs["query_sim"] = cosine_similarity(query_embedding, fs["embedding"])

    # Sort by similarity descending
    with_emb.sort(key=lambda x: x["query_sim"], reverse=True)

    # Recency dedup: for foresight about the same topic (cosine > 0.7),
    # keep only the most recent source_date. This ensures newer foresight
    # (e.g., "ACL injury" from Mar) supersedes older contradictory foresight
    # (e.g., "perfect health" from Feb) even without explicit conflict detection.
    def _get_source_date(fs):
        sd = fs.get("source_date")
        if sd is None:
            return date.min
        if isinstance(sd, datetime):
            return sd.date()
        if isinstance(sd, date):
            return sd
        try:
            return datetime.strptime(str(sd), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return date.min

    # Sort by source_date descending so newer items are processed first
    with_emb.sort(key=lambda x: _get_source_date(x), reverse=True)

    # First pass: recency dedup — for each topic cluster, keep only the newest
    recency_deduped = []
    for fs in with_emb:
        # Check if a more recent foresight about the same topic already exists
        is_stale = any(
            cosine_similarity(fs["embedding"], s["embedding"]) > 0.7
            and _get_source_date(s) > _get_source_date(fs)
            for s in recency_deduped
        )
        if not is_stale:
            recency_deduped.append(fs)

    # Re-sort by query similarity for final selection
    recency_deduped.sort(key=lambda x: x["query_sim"], reverse=True)

    # Second pass: near-duplicate dedup (>0.9) and select top results
    selected = []
    for fs in recency_deduped:
        if len(selected) >= FORESIGHT_MAX_RESULTS:
            break
        is_dup = any(
            cosine_similarity(fs["embedding"], s["embedding"]) > 0.9
            for s in selected
        )
        if not is_dup:
            selected.append(fs)

    # Fill remaining slots with non-embedded foresight (if any)
    remaining = FORESIGHT_MAX_RESULTS - len(selected)
    if remaining > 0 and without_emb:
        selected.extend(without_emb[:remaining])

    return selected
