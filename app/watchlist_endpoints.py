from __future__ import annotations

import re
from typing import Literal,  Optional, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import watchlist_store as ws

router = APIRouter(prefix="/watchlist", tags=["Watchlist"])

def clean_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9Xx]", "", (s or "")).strip().upper()

def infer_kind(key: str) -> Literal["asin","isbn","ebay_item","ebay_sold"]:
    k = clean_key(key)
    if len(k) == 10 and k.isalnum():
        return "asin"
    if len(k) in (10, 13) and all(ch.isdigit() or ch == "X" for ch in k):
        return "isbn"
    raise ValueError("Key must be a valid ASIN(10) or ISBN(10/13)")

class WatchUpsert(BaseModel):
    key: str = Field(..., description="ASIN or ISBN")
    interval_minutes: int = Field(..., gt=0)
    kind: Optional[Literal["asin","isbn","ebay_item","ebay_sold"]] = None
    start_in_minutes: Optional[int] = Field(None, ge=0)

class WatchEnable(BaseModel):
    enabled: bool = True

@router.get("")
def get_all():
    items = ws.list_items()
    return {"count": len(items), "items": [i.__dict__ for i in items]}

@router.post("")
def upsert(payload: WatchUpsert):
    key = clean_key(payload.key)
    try:
        kind = payload.kind or infer_kind(key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ws.upsert_item(
        key=key,
        kind=kind,
        interval_minutes=payload.interval_minutes,
        start_in_minutes=payload.start_in_minutes,
    )

@router.patch("/{key}")
def set_enabled(key: str, body: WatchEnable):
    key = clean_key(key)
    return ws.set_enabled(key, body.enabled)

@router.delete("/{key}")
def delete(key: str):
    key = clean_key(key)
    return ws.delete_item(key)
