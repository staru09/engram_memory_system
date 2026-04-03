import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
import vector_store
from backend.ingestion import periodic_ingestion_check
from backend.routes import threads, chat, stats, ingest, query


def _warm_connections():
    """Warm up external connections on startup so first request isn't slow."""
    try:
        db.get_user_profile()
        print("[warmup] PG connection pool warmed")
    except Exception as e:
        print(f"[warmup] PG warmup failed: {e}")
    try:
        client = vector_store._get_client()
        client.get_collection(vector_store.COLLECTION_NAME)
        print("[warmup] Qdrant connection warmed")
    except Exception as e:
        print(f"[warmup] Qdrant warmup failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    vector_store.init_collections()
    _warm_connections()
    threading.Thread(target=periodic_ingestion_check, daemon=True).start()
    yield
    db.close_pool()


app = FastAPI(title="Engram Memory Chat", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(threads.router)
app.include_router(chat.router)
app.include_router(stats.router)
app.include_router(ingest.router)
app.include_router(query.router)
