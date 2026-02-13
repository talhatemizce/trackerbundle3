from fastapi import FastAPI
from app.rules_endpoints import router as rules_router
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import os, json
from pathlib import Path
from typing import Optional, Dict

from app.rules_store import load_rules, set_defaults, set_isbn_override, delete_isbn_override, effective_limit

app = FastAPI(title="TrackerBundle Panel API", version="0.1.0")
app.include_router(rules_router)

DATA_PATH = Path(os.getenv("ISBN_STORE", "/home/ubuntu/trackerbundle3/app/data/isbns.json"))

def _load_isbns():
    if not DATA_PATH.exists():
        return []
    try:
        raw = DATA_PATH.read_text(encoding="utf-8").strip()
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
        return []
    except Exception:
        return []

def _save_isbns(isbns):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(sorted(set(isbns)), indent=2), encoding="utf-8")

class ISBNItem(BaseModel):
    isbn: str = Field(min_length=1)

class DefaultsPayload(BaseModel):
    new_max: Optional[float] = Field(default=None, gt=0)
    used_all_max: Optional[float] = Field(default=None, gt=0)
    used: Optional[Dict[str, Optional[float]]] = None
    multipliers: Optional[Dict[str, float]] = None

class ISBNRulePayload(BaseModel):
    new_max: Optional[float] = Field(default=None, gt=0)
    used_all_max: Optional[float] = Field(default=None, gt=0)
    used: Optional[Dict[str, Optional[float]]] = None

@app.get("/")
def home():
    return {
        "name": "TrackerBundle Panel API",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
        "isbns": "/isbns",
        "rules": "/rules",
        "effective": "/rules/effective?isbn=...&condition=brand_new|good|very_good|like_new|acceptable|used_all",
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
    }

@app.get("/isbns")
def list_isbns():
    items = _load_isbns()
    return {"ok": True, "count": len(items), "items": items}

@app.post("/isbns")
def add_isbn(item: ISBNItem):
    isbns = _load_isbns()
    isbns.append(item.isbn.strip())
    _save_isbns(isbns)
    return {"ok": True, "count": len(_load_isbns())}

@app.delete("/isbns/{isbn}")
def delete_isbn(isbn: str):
    isbns = [x for x in _load_isbns() if x != isbn]
    _save_isbns(isbns)
    return {"ok": True, "count": len(_load_isbns())}

@app.get("/rules", operation_id="get_rules_all")
def get_rules():
    return {"ok": True, **load_rules()}

@app.put("/rules/defaults")
def put_defaults(payload: DefaultsPayload):
    data = set_defaults(payload.model_dump())
    return {"ok": True, **data}

# IMPORTANT: this must be BEFORE /rules/{isbn}
@app.get("/rules/effective")
def get_effective(isbn: Optional[str] = None, condition: str = "used_all"):
    eff = effective_limit(isbn, condition)
    return {"ok": True, "isbn": isbn, "condition": condition, "effective": eff}

@app.get("/rules/{isbn}")
def get_isbn_rule(isbn: str):
    data = load_rules()
    ov = data["overrides"].get(isbn)
    return {"ok": True, "isbn": isbn, "override": ov}

@app.put("/rules/{isbn}")
def put_isbn_rule(isbn: str, payload: ISBNRulePayload):
    data = set_isbn_override(isbn, payload.model_dump())
    return {"ok": True, **data}

@app.delete("/rules/{isbn}")
def del_isbn_rule(isbn: str):
    data = delete_isbn_override(isbn)
    return {"ok": True, **data}
