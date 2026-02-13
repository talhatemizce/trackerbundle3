"""
Rules storage and logic for TrackerBundle
JSON-based price limit configuration with ISBN overrides
"""
import json
from typing import Dict, Any, Optional
from pathlib import Path

# Keep rules under app/data to match current repo convention
RULES_FILE = Path(__file__).resolve().parent / "data" / "rules.json"
USED_CONDITIONS = ["acceptable", "good", "very_good", "like_new"]

def _normalize_isbn(isbn: str) -> str:
    return (isbn or "").replace("-", "").replace(" ", "").strip()

def _normalize_condition(condition: str) -> Optional[str]:
    if not condition:
        return None
    c = condition.lower().strip().replace("-", "_").replace(" ", "_")
    if c in ("new", "brand_new", "brandnew"):
        return "brand_new"
    if c in USED_CONDITIONS:
        return c
    if c in ("used", "used_all"):
        return "used_all"
    return None

def save_rules(rules: Dict[str, Any]) -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")

def load_rules() -> Dict[str, Any]:
    if not RULES_FILE.exists():
        default_rules = {
            "defaults": {
                "new_max": 50.0,
                "used_all_max": 20.0,
                "used": {
                    "acceptable": 15.0,
                    "good": 18.0,
                    "very_good": 19.8,
                    "like_new": 21.78
                }
            },
            "overrides": {}
        }
        save_rules(default_rules)
        return default_rules

    raw = RULES_FILE.read_text(encoding="utf-8").strip()
    return json.loads(raw or "{}")

def effective_limit(isbn: Optional[str], condition: str) -> Dict[str, Any]:
    rules = load_rules()
    defaults = rules.get("defaults", {})
    overrides = rules.get("overrides", {})

    normalized_isbn = _normalize_isbn(isbn) if isbn else None
    override = overrides.get(normalized_isbn) if normalized_isbn else None

    norm_condition = _normalize_condition(condition)

    if norm_condition == "brand_new":
        if override and override.get("new_max") is not None:
            return {"kind": "brand_new", "limit": float(override["new_max"]), "source": "isbn.new_max"}
        return {"kind": "brand_new", "limit": float(defaults.get("new_max", 0.0)), "source": "defaults.new_max"}

    used_cond = norm_condition if norm_condition in USED_CONDITIONS else None

    if override:
        if used_cond and override.get("used", {}).get(used_cond) is not None:
            return {"kind": "used", "condition": used_cond, "limit": float(override["used"][used_cond]), "source": f"isbn.used.{used_cond}"}
        if override.get("used_all_max") is not None:
            return {"kind": "used", "condition": used_cond or "used_all", "limit": float(override["used_all_max"]), "source": "isbn.used_all_max"}

    if used_cond and defaults.get("used", {}).get(used_cond) is not None:
        return {"kind": "used", "condition": used_cond, "limit": float(defaults["used"][used_cond]), "source": f"defaults.used.{used_cond}"}

    return {"kind": "used", "condition": used_cond or "used_all", "limit": float(defaults.get("used_all_max", 0.0)), "source": "defaults.used_all_max"}

def set_defaults(
    new_max: Optional[float] = None,
    used_all_max: Optional[float] = None,
    used_conditions: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    rules = load_rules()
    rules.setdefault("defaults", {})
    rules["defaults"].setdefault("used", {})
    rules.setdefault("overrides", {})

    if new_max is not None:
        rules["defaults"]["new_max"] = float(new_max)
    if used_all_max is not None:
        rules["defaults"]["used_all_max"] = float(used_all_max)

    if used_conditions:
        for cond, price in used_conditions.items():
            norm_cond = _normalize_condition(cond)
            if norm_cond in USED_CONDITIONS:
                rules["defaults"]["used"][norm_cond] = float(price)

    save_rules(rules)
    return rules["defaults"]

def set_isbn_override(
    isbn: str,
    new_max: Optional[float] = None,
    used_all_max: Optional[float] = None,
    used_conditions: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    rules = load_rules()
    rules.setdefault("defaults", {})
    rules.setdefault("overrides", {})

    normalized_isbn = _normalize_isbn(isbn)
    rules["overrides"].setdefault(normalized_isbn, {})
    override = rules["overrides"][normalized_isbn]

    if new_max is not None:
        override["new_max"] = float(new_max)
    if used_all_max is not None:
        override["used_all_max"] = float(used_all_max)

    if used_conditions:
        override.setdefault("used", {})
        for cond, price in used_conditions.items():
            norm_cond = _normalize_condition(cond)
            if norm_cond in USED_CONDITIONS:
                override["used"][norm_cond] = float(price)

    save_rules(rules)
    return override

def delete_isbn_override(isbn: str) -> bool:
    rules = load_rules()
    rules.setdefault("overrides", {})
    normalized_isbn = _normalize_isbn(isbn)
    if normalized_isbn in rules["overrides"]:
        del rules["overrides"][normalized_isbn]
        save_rules(rules)
        return True
    return False
