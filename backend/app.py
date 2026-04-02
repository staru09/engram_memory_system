import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
import vector_store
from backend.ingestion import periodic_ingestion_check
from backend.routes import threads, chat, stats, ingest, query


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    vector_store.init_collections()
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
