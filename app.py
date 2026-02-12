from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, json
from datetime import datetime, timezone
from pathlib import Path

APP_TITLE = "TrackerBundle Panel API"
DATA_FILE = Path(__file__).with_name("isbns.json")

app = FastAPI(title=APP_TITLE)

class ISBNIn(BaseModel):
    isbn: str

def _load():
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []

def _save(items):
    DATA_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

def _norm(isbn: str) -> str:
    return "".join(ch for ch in isbn.strip() if ch.isdigit() or ch.upper() == "X")

@app.get("/")
def home():
    return {
        "service": "trackerbundle-panel",
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
        "isbn_list": "/isbns",
        "isbn_add": "/isbns (POST)",
        "isbn_del": "/isbns/{isbn} (DELETE)",
    }

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/status")
def status():
    return {
        "ok": True,
        "service": "trackerbundle-panel",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "has_bot_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "isbn_count": len(_load()),
    }

@app.get("/isbns")
def list_isbns():
    return {"count": len(_load()), "items": _load()}

@app.post("/isbns")
def add_isbn(payload: ISBNIn):
    isbn = _norm(payload.isbn)
    if not isbn:
        raise HTTPException(400, "isbn empty")
    items = _load()
    if isbn in items:
        return {"ok": True, "added": False, "isbn": isbn, "count": len(items)}
    items.append(isbn)
    _save(items)
    return {"ok": True, "added": True, "isbn": isbn, "count": len(items)}

@app.delete("/isbns/{isbn}")
def delete_isbn(isbn: str):
    isbn = _norm(isbn)
    items = _load()
    if isbn not in items:
        return {"ok": True, "deleted": False, "isbn": isbn, "count": len(items)}
    items = [x for x in items if x != isbn]
    _save(items)
    return {"ok": True, "deleted": True, "isbn": isbn, "count": len(items)}
