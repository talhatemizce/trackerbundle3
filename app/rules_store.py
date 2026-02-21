"""
Rules storage and logic for TrackerBundle
JSON-based price limit + scan-interval configuration with per-ISBN overrides.

JSON structure (app/data/rules.json):
{
  "defaults": {
    "new_max": 50.0,
    "used_all_max": 20.0,
    "interval_seconds": 300,
    "used": {
      "acceptable": 15.0,
      "good": 18.0,
      "very_good": 19.8,
      "like_new": 21.78
    }
  },
  "overrides": {
    "<isbn>": {
      "new_max": 30.0,          # optional
      "used_all_max": 15.0,     # optional
      "interval_seconds": 600,  # optional
      "used": { ... }           # optional per-condition prices
    }
  }
}
"""
import os
from types import SimpleNamespace
from typing import Any, Dict, Optional
from pathlib import Path

# Keep rules under app/data to match current repo convention
RULES_FILE = Path(__file__).resolve().parent / "data" / "rules.json"
USED_CONDITIONS = ["acceptable", "good", "very_good", "like_new"]

# Global fallback interval (overridden by env var or rules.json defaults)
_DEFAULT_INTERVAL = int(os.getenv("SCHED_TICK_SECONDS", "300"))


# ── Internal helpers ───────────────────────────────────────────────────────────

_PRICE_MIN = 0.01
_PRICE_MAX = 9_999.0
_INTERVAL_MIN = 60        # 1 dakika
_INTERVAL_MAX = 86_400 * 30  # 30 gün


def _valid_price(value: float, name: str) -> float:
    v = float(value)
    if not (_PRICE_MIN <= v <= _PRICE_MAX):
        raise ValueError(f"{name} must be between {_PRICE_MIN} and {_PRICE_MAX}, got {v}")
    return round(v, 2)


def _valid_interval(value: int, name: str = "interval_seconds") -> int:
    v = int(value)
    if not (_INTERVAL_MIN <= v <= _INTERVAL_MAX):
        raise ValueError(f"{name} must be between {_INTERVAL_MIN}s and {_INTERVAL_MAX}s, got {v}")
    return v


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


# ── File I/O ───────────────────────────────────────────────────────────────────

def save_rules(rules: Dict[str, Any]) -> None:
    import json
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")


def load_rules() -> Dict[str, Any]:
    import json
    if not RULES_FILE.exists():
        default_rules = {
            "defaults": {
                "new_max": 50.0,
                "used_all_max": 20.0,
                "interval_seconds": _DEFAULT_INTERVAL,
                "used": {
                    "acceptable": 15.0,
                    "good": 18.0,
                    "very_good": 19.8,
                    "like_new": 21.78,
                },
            },
            "overrides": {},
        }
        save_rules(default_rules)
        return default_rules

    raw = RULES_FILE.read_text(encoding="utf-8").strip()
    return json.loads(raw or "{}")


# ── Price limit resolution (used by scheduler) ─────────────────────────────────

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


# ── Panel/API interface ────────────────────────────────────────────────────────

def list_intervals() -> Dict[str, Any]:
    """
    Return a dict of all per-ISBN rules for the panel watchlist table.
    {
      isbn: {
        "interval_seconds": int,
        "new_max": float | None,
        "used_all_max": float | None,
      },
      ...
    }
    """
    rules = load_rules()
    defaults = rules.get("defaults", {})
    overrides = rules.get("overrides", {})
    default_interval = int(defaults.get("interval_seconds", _DEFAULT_INTERVAL))

    result: Dict[str, Any] = {}
    for isbn, ov in overrides.items():
        result[isbn] = {
            "interval_seconds": int(ov.get("interval_seconds") or default_interval),
            "new_max": float(ov["new_max"]) if ov.get("new_max") is not None else None,
            "used_all_max": float(ov["used_all_max"]) if ov.get("used_all_max") is not None else None,
        }
    return result


def get_rule(isbn: str) -> SimpleNamespace:
    """
    Return rule for a single ISBN as a SimpleNamespace with attributes:
      .interval_seconds  int
      .new_max           float
      .used_all_max      float
    Falls back to defaults when no per-ISBN override is present.
    """
    rules = load_rules()
    defaults = rules.get("defaults", {})
    overrides = rules.get("overrides", {})

    normalized = _normalize_isbn(isbn)
    ov = overrides.get(normalized, {})

    interval_seconds = int(
        ov.get("interval_seconds")
        or defaults.get("interval_seconds")
        or _DEFAULT_INTERVAL
    )
    new_max = float(
        ov["new_max"] if ov.get("new_max") is not None
        else defaults.get("new_max", 50.0)
    )
    used_all_max = float(
        ov["used_all_max"] if ov.get("used_all_max") is not None
        else defaults.get("used_all_max", 20.0)
    )

    return SimpleNamespace(
        interval_seconds=interval_seconds,
        new_max=new_max,
        used_all_max=used_all_max,
    )


def set_interval(isbn: str, interval_seconds: int) -> None:
    """Persist per-ISBN scan interval in overrides."""
    validated = _valid_interval(interval_seconds)
    rules = load_rules()
    rules.setdefault("overrides", {})
    normalized = _normalize_isbn(isbn)
    rules["overrides"].setdefault(normalized, {})
    rules["overrides"][normalized]["interval_seconds"] = validated
    save_rules(rules)


def set_override(
    isbn: str,
    new_max: Optional[float] = None,
    used_all_max: Optional[float] = None,
) -> None:
    """
    Persist per-ISBN price override.
    Thin wrapper that delegates to set_isbn_override for consistency.
    """
    set_isbn_override(isbn, new_max=new_max, used_all_max=used_all_max)


# ── Fine-grained setters (used by /rules/* endpoints and direct callers) ───────

def set_defaults(
    new_max: Optional[float] = None,
    used_all_max: Optional[float] = None,
    interval_seconds: Optional[int] = None,
    used_conditions: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    rules = load_rules()
    rules.setdefault("defaults", {})
    rules["defaults"].setdefault("used", {})
    rules.setdefault("overrides", {})

    if new_max is not None:
        rules["defaults"]["new_max"] = _valid_price(new_max, "new_max")
    if used_all_max is not None:
        rules["defaults"]["used_all_max"] = _valid_price(used_all_max, "used_all_max")
    if interval_seconds is not None:
        rules["defaults"]["interval_seconds"] = _valid_interval(interval_seconds)

    if used_conditions:
        for cond, price in used_conditions.items():
            norm_cond = _normalize_condition(cond)
            if norm_cond in USED_CONDITIONS:
                rules["defaults"]["used"][norm_cond] = _valid_price(price, f"used.{norm_cond}")

    save_rules(rules)
    return rules["defaults"]


def set_isbn_override(
    isbn: str,
    new_max: Optional[float] = None,
    used_all_max: Optional[float] = None,
    used_conditions: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    rules = load_rules()
    rules.setdefault("defaults", {})
    rules.setdefault("overrides", {})

    normalized_isbn = _normalize_isbn(isbn)
    rules["overrides"].setdefault(normalized_isbn, {})
    override = rules["overrides"][normalized_isbn]

    if new_max is not None:
        override["new_max"] = _valid_price(new_max, "new_max")
    if used_all_max is not None:
        override["used_all_max"] = _valid_price(used_all_max, "used_all_max")

    if used_conditions:
        override.setdefault("used", {})
        for cond, price in used_conditions.items():
            norm_cond = _normalize_condition(cond)
            if norm_cond in USED_CONDITIONS:
                override["used"][norm_cond] = _valid_price(price, f"used.{norm_cond}")

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
