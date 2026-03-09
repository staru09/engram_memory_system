import math
from datetime import datetime, date
from config import RRF_K, RETRIEVAL_TOP_K, RRF_KEYWORD_WEIGHT, RRF_VECTOR_WEIGHT, FACT_DEDUP_THRESHOLD
import db
import vector_store
from agentic_layer.vectorize_service import embed_text


FORESIGHT_MAX_RESULTS = 5


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors (pure Python)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def keyword_search(query: str, top_k: int = RETRIEVAL_TOP_K, query_time=None) -> list[dict]:
    """
    PostgreSQL full-text search on atomic_facts using ts_rank.
    Returns list of {fact_id, memcell_id, fact_text, conversation_date, rank}.
    """
    return db.keyword_search_facts(query, top_k, query_time=query_time)


def vector_search(query_embedding: list[float], top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    Qdrant cosine similarity search using a pre-computed query embedding.
    Returns list of {fact_id, memcell_id, score}.
    """
    return vector_store.search_facts(query_embedding, top_k)


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
    for rank, result in enumerate(keyword_results, start=1):
        fid = result["id"]
        if fid not in scores:
            scores[fid] = {"fact_id": fid, "memcell_id": result["memcell_id"],
                           "fact_text": result["fact_text"], "rrf_score": 0.0,
                           "conversation_date": result.get("conversation_date")}
        scores[fid]["rrf_score"] += RRF_KEYWORD_WEIGHT / (k + rank)

    # Score from vector results (baseline weight)
    for rank, result in enumerate(vector_results, start=1):
        fid = result["fact_id"]
        if fid not in scores:
            # Need to fetch fact_text and conversation_date from DB
            fact = db.get_fact_by_id(fid)
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
                  query_time=None, query_embedding=None) -> list[dict]:
    """
    Run both keyword + vector search and fuse via RRF.
    Accepts optional pre-computed query_embedding to avoid redundant embed calls.
    """
    if query_embedding is None:
        query_embedding = embed_text(query)

    kw_results = keyword_search(query, top_k, query_time=query_time)
    vec_results = vector_search(query_embedding, top_k)
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


def filter_active_foresight(query_time: datetime = None,
                            query_embedding: list[float] = None) -> list[dict]:
    """
    Return foresight signals valid at query_time, ranked by semantic similarity
    to the query and deduplicated.

    Uses cosine similarity between query embedding and foresight embeddings
    (stored at ingestion time). Near-duplicate foresight (pairwise cosine > 0.9)
    are collapsed to the highest-scoring entry.
    """
    if query_time is None:
        query_time = datetime.now()

    all_foresight = db.get_active_foresight(query_time)

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

    # Deduplicate: skip if cosine > 0.9 with any already-selected foresight
    selected = []
    for fs in with_emb:
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
