from fastapi import APIRouter

import db

router = APIRouter()


@router.get("/ping")
def ping():
    return {"status": "pong"}


@router.get("/stats")
def get_stats():
    return db.get_system_stats()
