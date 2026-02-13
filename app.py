from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

app = FastAPI(title="TrackerBundle Panel API", version="0.1.0")

BASE_DIR = Path(__file__).resolve().parent
ISBN_FILE = BASE_DIR / "isbns.json"
RULES_FILE = BASE_DIR / "rules.json"

DEFAULT_RULES = {"new_max": 50.0, "used_max": 20.0}

class ISBNItem(BaseModel):
    isbn: str

class RulesPayload(BaseModel):
    new_max: float
    used_max: float

def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _read_json(path: Path, default: Any):
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default

def load_isbns() -> List[str]:
    data = _read_json(ISBN_FILE, [])
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    return []

def save_isbns(isbns: List[str]) -> None:
    _atomic_write_json(ISBN_FILE, isbns)

def load_rules() -> Dict[str, float]:
    data = _read_json(RULES_FILE, DEFAULT_RULES)
    if not isinstance(data, dict):
        data = DEFAULT_RULES
    return {
        "new_max": float(data.get("new_max", DEFAULT_RULES["new_max"])),
        "used_max": float(data.get("used_max", DEFAULT_RULES["used_max"])),
    }

def save_rules(rules: Dict[str, float]) -> None:
    _atomic_write_json(RULES_FILE, rules)

@app.get("/")
def home():
    return (
        "<h1>TrackerBundle Panel API</h1>"
        "<p>OK ✅</p>"
        "<ul>"
        '<li><a href="/docs">Docs (Swagger)</a></li>'
        '<li><a href="/status">Status</a></li>'
        '<li><a href="/health">Health</a></li>'
        '<li><a href="/rules">Rules</a></li>'
        '<li><a href="/isbns">ISBNs</a></li>'
        "</ul>"
    )

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
        "isbn_count": len(load_isbns()),
    }

@app.get("/isbns")
def list_isbns():
    return {"ok": True, "items": load_isbns()}

@app.post("/isbns")
def add_isbn(item: ISBNItem):
    isbn = item.isbn.strip()
    if not isbn:
        raise HTTPException(status_code=400, detail="isbn empty")
    items = load_isbns()
    if isbn not in items:
        items.append(isbn)
        save_isbns(items)
    return {"ok": True, "count": len(items), "items": items[-10:]}

@app.delete("/isbns/{isbn}")
def delete_isbn(isbn: str):
    isbn = isbn.strip()
    items = load_isbns()
    new_items = [x for x in items if x != isbn]
    if len(new_items) != len(items):
        save_isbns(new_items)
    return {"ok": True, "count": len(new_items)}

@app.get("/rules")
def get_rules():
    r = load_rules()
    return {"ok": True, **r}

@app.put("/rules")
def set_rules(payload: RulesPayload):
    r = {"new_max": float(payload.new_max), "used_max": float(payload.used_max)}
    save_rules(r)
    return {"ok": True, **r}

# ==== RULES API ====
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any
from rules_store import load_rules, get_isbn_rules, set_rule, delete_rule

Condition = Literal["new","used_like_new","used_very_good","used_good","used_acceptable"]

class RuleUpsert(BaseModel):
    max_price: Optional[float] = Field(default=None, ge=0)

@app.get("/rules")
def rules_all() -> Dict[str, Any]:
    return load_rules()

@app.get("/rules/{isbn}")
def rules_for_isbn(isbn: str) -> Dict[str, Any]:
    return {"isbn": isbn, "rules": get_isbn_rules(isbn)}

@app.put("/rules/{isbn}/{condition}")
def rules_set(isbn: str, condition: Condition, body: RuleUpsert):
    rules = set_rule(isbn, condition, body.max_price)
    return {"ok": True, "isbn": isbn, "rules": rules}

@app.delete("/rules/{isbn}/{condition}")
def rules_del(isbn: str, condition: Condition):
    rules = delete_rule(isbn, condition)
    return {"ok": True, "isbn": isbn, "rules": rules}
