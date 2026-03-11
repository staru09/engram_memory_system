from fastapi import APIRouter

import db

router = APIRouter()


@router.get("/stats")
def get_stats():
    return db.get_system_stats()
