import json
import os
from threading import Lock
from typing import Any, Dict, Optional

RULES_PATH = os.getenv("RULES_PATH", "/home/ubuntu/trackerbundle3/data/rules.json")
_lock = Lock()

def _ensure_file():
    os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
    if not os.path.exists(RULES_PATH):
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            json.dump({"defaults": {}, "by_isbn": {}}, f, ensure_ascii=False, indent=2)

def load_rules() -> Dict[str, Any]:
    with _lock:
        _ensure_file()
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

def save_rules(data: Dict[str, Any]) -> None:
    with _lock:
        _ensure_file()
        tmp = RULES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, RULES_PATH)

def get_isbn_rules(isbn: str) -> Dict[str, Any]:
    data = load_rules()
    return data.get("by_isbn", {}).get(isbn, {})

def set_rule(isbn: str, condition: str, max_price: Optional[float]) -> Dict[str, Any]:
    data = load_rules()
    data.setdefault("defaults", {})
    data.setdefault("by_isbn", {})
    data["by_isbn"].setdefault(isbn, {})
    data["by_isbn"][isbn][condition] = max_price
    save_rules(data)
    return data["by_isbn"][isbn]

def delete_rule(isbn: str, condition: str) -> Dict[str, Any]:
    data = load_rules()
    by_isbn = data.get("by_isbn", {})
    if isbn in by_isbn and condition in by_isbn[isbn]:
        del by_isbn[isbn][condition]
        if not by_isbn[isbn]:
            del by_isbn[isbn]
        save_rules(data)
    return by_isbn.get(isbn, {})
