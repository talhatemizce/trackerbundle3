from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .watch_store import add_item, delete_item, list_items

router = APIRouter(prefix="/ebay/watch", tags=["ebay-watch"])

class WatchCreate(BaseModel):
    query: str = Field(..., min_length=3, description="ISBN/ASIN/keyword/url")
    interval_sec: int = Field(..., ge=60, le=60*60*24*30, description="min 60s, max 30 days")
    enabled: bool = True
    note: str = ""

@router.get("")
def get_watch():
    return {"items": list_items()}

@router.post("")
def create_watch(payload: WatchCreate):
    return add_item(payload.query, payload.interval_sec, payload.enabled, payload.note)

@router.delete("/{item_id}")
def remove_watch(item_id: str):
    ok = delete_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True, "id": item_id}
