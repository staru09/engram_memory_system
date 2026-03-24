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

_foresight_cache = {"data": None, "embeddings": None, "ts": 0}


def _get_foresight_cached(query_time) -> list[dict]:
    """Return foresight with in-memory caching (60s TTL)."""
    now = _time.time()
    if _foresight_cache["data"] is not None and now - _foresight_cache["ts"] < FORESIGHT_CACHE_TTL:
        return _foresight_cache["data"]
    data = db.get_active_foresight(query_time)
    _foresight_cache["data"] = data
    _foresight_cache["ts"] = now
    return data


def invalidate_foresight_cache():
    """Call after ingestion to force fresh foresight on next query."""
    _foresight_cache["data"] = None
    _foresight_cache["embeddings"] = None
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
                   date_filter: dict = None, category_filter: list[str] = None) -> list[dict]:
    """
    Qdrant cosine similarity search using a pre-computed query embedding.
    Optional category_filter narrows search to specific fact categories.
    Returns list of {fact_id, memcell_id, score}.
    """
    return vector_store.search_facts(query_embedding, top_k, date_filter=date_filter,
                                     category_filter=category_filter)


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

    # Score from vector results (baseline weight)
    # fact_text and conversation_date now come from Qdrant payload — no DB call needed
    vec_only_ids = []
    for rank, result in enumerate(vector_results, start=1):
        fid = result["fact_id"]
        if fid not in scores:
            fact_text = result.get("fact_text", "")
            conv_date = result.get("conversation_date_str")
            # Fallback: if Qdrant payload doesn't have fact_text (old data), mark for DB fetch
            if not fact_text:
                vec_only_ids.append(fid)
            scores[fid] = {"fact_id": fid, "memcell_id": result["memcell_id"],
                           "fact_text": fact_text, "rrf_score": 0.0,
                           "conversation_date": conv_date}
        scores[fid]["rrf_score"] += RRF_VECTOR_WEIGHT / (k + rank)

    # Fallback DB fetch only for facts without payload data (old data before migration)
    if vec_only_ids:
        vec_facts_map = db.get_facts_by_ids(vec_only_ids)
        for fid, fact in vec_facts_map.items():
            if fid in scores and not scores[fid]["fact_text"]:
                scores[fid]["fact_text"] = fact.get("fact_text", "")
                scores[fid]["conversation_date"] = fact.get("conversation_date")

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

    # Run keyword + vector search in parallel with individual timing
    t_parallel_start = _time.time()
    kw_timing = {}
    vec_timing = {}

    def _timed_keyword():
        t = _time.time()
        results = keyword_search(query, top_k, query_time, date_filter)
        kw_timing["duration"] = _time.time() - t
        return results

    def _timed_vector():
        t = _time.time()
        results = vector_search(query_embedding, top_k, date_filter)
        vec_timing["duration"] = _time.time() - t
        return results

    with ThreadPoolExecutor(max_workers=2) as executor:
        kw_future = executor.submit(_timed_keyword)
        vec_future = executor.submit(_timed_vector)
        kw_results = kw_future.result()
        vec_results = vec_future.result()
    parallel_total = _time.time() - t_parallel_start
    print(f"    [hybrid] Keyword: {kw_timing['duration']:.2f}s ({len(kw_results)} results)")
    print(f"    [hybrid] Vector:  {vec_timing['duration']:.2f}s ({len(vec_results)} results)")
    print(f"    [hybrid] Parallel total: {parallel_total:.2f}s")

    t0 = _time.time()
    kw_fact_ids = {r["id"] for r in kw_results}
    fused = rrf_fusion(kw_results, vec_results)
    print(f"    [hybrid] RRF fusion ({len(fused)} merged): {_time.time() - t0:.2f}s")

    # Post-fusion temporal filter — only for vector-only facts
    # Keyword results are already filtered by conversation_date + superseded_on in SQL
    if query_time:
        vec_only_in_fused = [f["fact_id"] for f in fused if f["fact_id"] not in kw_fact_ids]
        if vec_only_in_fused:
            t0 = _time.time()
            valid_vec_ids = db.filter_facts_by_time(vec_only_in_fused, query_time)
            fused = [f for f in fused if f["fact_id"] in kw_fact_ids or f["fact_id"] in valid_vec_ids]
            print(f"    [hybrid] Post-fusion filter ({len(vec_only_in_fused)} vec-only → {len([f for f in fused if f['fact_id'] not in kw_fact_ids])} valid): {_time.time() - t0:.2f}s")
        else:
            print(f"    [hybrid] Post-fusion filter: skipped (all facts from keyword)")

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


def filter_active_foresight(query_time: datetime = None,
                            query_embedding: list[float] = None) -> list[dict]:
    """
    Return foresight signals valid at query_time, ranked by semantic similarity
    to the query and deduplicated.

    Args:
        query_time: IST datetime for all comparisons (conversation_date, valid_from/valid_until)
        query_embedding: Pre-computed query embedding for relevance scoring
    """
    if query_time is None:
        from datetime import timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        query_time = datetime.now(IST).replace(tzinfo=None)

    all_foresight = _get_foresight_cached(query_time)

    if not query_embedding or not all_foresight:
        return all_foresight

    # Lazy-load embeddings: cached separately from metadata to keep metadata fetch fast (~0.25s vs ~1.3s)
    if _foresight_cache["embeddings"] is None:
        foresight_ids = [fs["id"] for fs in all_foresight]
        _foresight_cache["embeddings"] = db.get_foresight_embeddings(foresight_ids)
    embedding_map = _foresight_cache["embeddings"]
    for fs in all_foresight:
        fs["embedding"] = embedding_map.get(fs["id"])

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
    def _get_recency_key(fs):
        """Return (source_date, created_at) for recency comparison.
        Uses source_date (DATE) first, then created_at (TIMESTAMP) as tiebreaker
        for foresight ingested on the same day."""
        sd = fs.get("source_date")
        if sd is None:
            source_d = date.min
        elif isinstance(sd, datetime):
            source_d = sd.date()
        elif isinstance(sd, date):
            source_d = sd
        else:
            try:
                source_d = datetime.strptime(str(sd), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                source_d = date.min

        ca = fs.get("created_at")
        if ca is None:
            created = datetime.min
        elif isinstance(ca, datetime):
            created = ca
        else:
            created = datetime.min

        return (source_d, created)

    # Sort by recency descending (source_date first, created_at as tiebreaker)
    with_emb.sort(key=lambda x: _get_recency_key(x), reverse=True)

    # First pass: recency dedup — for each topic cluster, keep only the newest
    recency_deduped = []
    for fs in with_emb:
        # Check if a more recent foresight about the same topic already exists
        is_stale = any(
            cosine_similarity(fs["embedding"], s["embedding"]) > 0.7
            and _get_recency_key(s) > _get_recency_key(fs)
            for s in recency_deduped
        )
        if not is_stale:
            recency_deduped.append(fs)

    # Re-sort by query similarity for final selection
    recency_deduped.sort(key=lambda x: x["query_sim"], reverse=True)

    # Filter out foresight with very low query relevance
    recency_deduped = [fs for fs in recency_deduped if fs["query_sim"] >= 0.3]

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
