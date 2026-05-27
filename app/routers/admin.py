"""Admin endpoints for manual batch triggering and inspection."""
from fastapi import APIRouter, HTTPException

from ..batch import parse_day


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/parse-day/{day}")
async def trigger_parse_day(day: str):
    """Manually parse a single day-bucket. `day` is 'YYYY-MM-DD'.
    Idempotent — existing rows for the day are replaced."""
    try:
        return parse_day(day)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse failed: {e}")
