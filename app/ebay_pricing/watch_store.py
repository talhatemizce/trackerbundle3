from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

WATCH_FILE = Path(__file__).resolve().parents[2] / "data" / "ebay_watch.json"

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()

def _ensure_file() -> Dict[str, Any]:
    if not WATCH_FILE.exists():
        WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"items": []}
        WATCH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    return json.loads(WATCH_FILE.read_text(encoding="utf-8"))

def load_watch() -> Dict[str, Any]:
    return _ensure_file()

def save_watch(data: Dict[str, Any]) -> Dict[str, Any]:
    WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCH_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data

def list_items() -> List[Dict[str, Any]]:
    data = load_watch()
    return list(data.get("items") or [])

def add_item(query: str, interval_sec: int, enabled: bool = True, note: str = "") -> Dict[str, Any]:
    data = load_watch()
    now = _utcnow()
    item = {
        "id": str(uuid.uuid4())[:8],
        "query": (query or "").strip(),
        "interval_sec": int(interval_sec),
        "enabled": bool(enabled),
        "note": note or "",
        "last_run_utc": None,
        "next_run_utc": _iso(now + timedelta(seconds=int(interval_sec))),
        "last_digest": None,
    }
    data["items"] = (data.get("items") or [])
    data["items"].append(item)
    save_watch(data)
    return item

def delete_item(item_id: str) -> bool:
    data = load_watch()
    items = data.get("items") or []
    before = len(items)
    data["items"] = [x for x in items if x.get("id") != item_id]
    if len(data["items"]) == before:
        return False
    save_watch(data)
    return True
