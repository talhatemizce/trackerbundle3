from fastapi import APIRouter
from datetime import datetime, timezone
from app.core.config import get_settings

router = APIRouter()

@router.get("/health")
def health():
    return {"ok": True}

@router.get("/status")
def status():
    s = get_settings()
    return {
        "ok": True,
        "service": "trackerbundle-panel",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "has_bot_token": bool(s.telegram_bot_token),
    }
