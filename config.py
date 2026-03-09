import os
from dotenv import load_dotenv

load_dotenv()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# PostgreSQL
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "54345"))
PG_USER = os.getenv("PG_USER", "evermemos")
PG_PASSWORD = os.getenv("PG_PASSWORD", "evermemos")
PG_DB = os.getenv("PG_DB", "evermemos")

# Qdrant
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

# Clustering
SCENE_SIMILARITY_THRESHOLD = float(os.getenv("SCENE_SIMILARITY_THRESHOLD", "0.75"))

# Retrieval
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "10"))
SCENE_TOP_N = int(os.getenv("SCENE_TOP_N", "5"))
RRF_K = int(os.getenv("RRF_K", "60"))  # RRF constant
RRF_KEYWORD_WEIGHT = float(os.getenv("RRF_KEYWORD_WEIGHT", "1.5"))
RRF_VECTOR_WEIGHT = float(os.getenv("RRF_VECTOR_WEIGHT", "1.0"))
FACT_DEDUP_THRESHOLD = float(os.getenv("FACT_DEDUP_THRESHOLD", "0.9"))

# Embedding dimension (gemini-embedding-001 outputs 3072-dim by default)
EMBEDDING_DIM = 3072
