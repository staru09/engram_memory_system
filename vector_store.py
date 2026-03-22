from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, Range,
    PayloadSchemaType
)
from config import QDRANT_HOST, QDRANT_PORT, QDRANT_URL, QDRANT_API_KEY, EMBEDDING_DIM


_client = None

def get_client() -> QdrantClient:
    global _client
    if _client is None:
        if QDRANT_URL:
            _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
        else:
            _client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False, timeout=30)
    return _client


def init_collections():
    """Create Qdrant collections if they don't exist, and ensure payload indexes."""
    client = get_client()
    for name in ("facts", "scenes"):
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
                timeout=60,
            )
    try:
        client.create_payload_index(
            collection_name="facts",
            field_name="conversation_date",
            field_schema=PayloadSchemaType.INTEGER,
        )
    except Exception:
        pass

def _date_to_int(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' to integer YYYYMMDD for Qdrant numeric filtering."""
    return int(date_str.replace("-", ""))


def upsert_fact(fact_id: int, memcell_id: int, embedding: list[float],
                conversation_date: str = None, fact_text: str = None):
    client = get_client()
    payload = {"fact_id": fact_id, "memcell_id": memcell_id}
    if conversation_date:
        payload["conversation_date"] = _date_to_int(conversation_date)
        payload["conversation_date_str"] = conversation_date  # for display in RRF results
    if fact_text:
        payload["fact_text"] = fact_text
    client.upsert(
        collection_name="facts",
        points=[
            PointStruct(
                id=fact_id,
                vector=embedding,
                payload=payload,
            )
        ],
    )


def upsert_scene(scene_id: int, embedding: list[float]):
    client = get_client()
    client.upsert(
        collection_name="scenes",
        points=[
            PointStruct(
                id=scene_id,
                vector=embedding,
                payload={"memscene_id": scene_id},
            )
        ],
    )


def search_facts(query_embedding: list[float], top_k: int = 10,
                  date_filter: dict = None) -> list[dict]:
    """Semantic search over atomic fact embeddings. Returns list of {fact_id, memcell_id, score}.

    Args:
        date_filter: Optional {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}
    """
    client = get_client()
    query_filter = None
    if date_filter:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="conversation_date",
                    range=Range(
                        gte=_date_to_int(date_filter["date_from"]),
                        lte=_date_to_int(date_filter["date_to"]),
                    ),
                )
            ]
        )
    results = client.query_points(
        collection_name="facts",
        query=query_embedding,
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    )
    return [
        {
            "fact_id": hit.payload["fact_id"],
            "memcell_id": hit.payload["memcell_id"],
            "score": hit.score,
            "fact_text": hit.payload.get("fact_text", ""),
            "conversation_date_str": hit.payload.get("conversation_date_str"),
        }
        for hit in results.points
    ]


def get_fact_embeddings(fact_ids: list[int]) -> dict[int, list[float]]:
    """Batch retrieve fact embeddings from Qdrant by point IDs."""
    if not fact_ids:
        return {}
    client = get_client()
    points = client.retrieve(collection_name="facts", ids=fact_ids, with_vectors=True)
    return {p.id: p.vector for p in points}


def search_nearest_scene(query_embedding: list[float], top_k: int = 1) -> list[dict]:
    """Find nearest MemScene centroid. Returns list of {memscene_id, score}."""
    client = get_client()
    results = client.query_points(
        collection_name="scenes",
        query=query_embedding,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "memscene_id": hit.payload["memscene_id"],
            "score": hit.score,
        }
        for hit in results.points
    ]
