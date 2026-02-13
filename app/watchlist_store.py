from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "watchlist.db"

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def dt_to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def iso_to_dt(s: str) -> datetime:
    # expects ISO with Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              key TEXT NOT NULL UNIQUE,               -- ASIN or ISBN normalized
              kind TEXT NOT NULL,                     -- 'asin' or 'isbn'
              interval_minutes INTEGER NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              next_run_utc TEXT NOT NULL,
              last_run_utc TEXT,
              last_status INTEGER,
              last_error TEXT,
              last_payload_json TEXT
            );
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_next_run ON watchlist(enabled, next_run_utc);")

@dataclass
class WatchItem:
    id: int
    key: str
    kind: str
    interval_minutes: int
    enabled: bool
    next_run_utc: str
    last_run_utc: Optional[str]
    last_status: Optional[int]
    last_error: Optional[str]
    last_payload_json: Optional[str]

def _row_to_item(r: sqlite3.Row) -> WatchItem:
    return WatchItem(
        id=r["id"],
        key=r["key"],
        kind=r["kind"],
        interval_minutes=r["interval_minutes"],
        enabled=bool(r["enabled"]),
        next_run_utc=r["next_run_utc"],
        last_run_utc=r["last_run_utc"],
        last_status=r["last_status"],
        last_error=r["last_error"],
        last_payload_json=r["last_payload_json"],
    )

def list_items() -> List[WatchItem]:
    ensure_db()
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("SELECT * FROM watchlist ORDER BY enabled DESC, next_run_utc ASC, id ASC;")
        return [_row_to_item(r) for r in cur.fetchall()]

def upsert_item(key: str, kind: str, interval_minutes: int, start_in_minutes: Optional[int] = None) -> Dict[str, Any]:
    ensure_db()
    if kind not in ("asin", "isbn", "ebay_item", "ebay_sold"):
        raise ValueError("kind must be 'asin'/'isbn'/'ebay_item'/'ebay_sold'")
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")

    now = utc_now()
    if start_in_minutes is None:
        # "jitter": aynı anda hepsi çalışmasın diye 0..interval/4 arası dağıt
        jitter = max(1, min(interval_minutes // 4, 60))
        start_in_minutes = (hash(key) % jitter)

    next_run = now + timedelta(minutes=int(start_in_minutes))
    next_run_s = dt_to_iso(next_run)

    with sqlite3.connect(DB_PATH) as con:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute(
            """
            INSERT INTO watchlist(key, kind, interval_minutes, enabled, next_run_utc)
            VALUES(?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                kind=excluded.kind,
                interval_minutes=excluded.interval_minutes,
                enabled=1,
                next_run_utc=excluded.next_run_utc
            """,
            (key, kind, int(interval_minutes), 1, next_run_s),
        )
    return {"ok": True, "key": key, "kind": kind, "interval_minutes": interval_minutes, "next_run_utc": next_run_s}

def set_enabled(key: str, enabled: bool) -> Dict[str, Any]:
    ensure_db()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("UPDATE watchlist SET enabled=? WHERE key=?;", (1 if enabled else 0, key))
        if cur.rowcount == 0:
            return {"ok": False, "detail": "not_found"}
    return {"ok": True, "key": key, "enabled": enabled}

def delete_item(key: str) -> Dict[str, Any]:
    ensure_db()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("DELETE FROM watchlist WHERE key=?;", (key,))
        if cur.rowcount == 0:
            return {"ok": False, "detail": "not_found"}
    return {"ok": True, "key": key}

def due_items(limit: int) -> List[WatchItem]:
    ensure_db()
    now_s = dt_to_iso(utc_now())
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT * FROM watchlist
            WHERE enabled=1 AND next_run_utc <= ?
            ORDER BY next_run_utc ASC, id ASC
            LIMIT ?;
            """,
            (now_s, int(limit)),
        )
        return [_row_to_item(r) for r in cur.fetchall()]

def mark_result(
    key: str,
    status_code: Optional[int],
    payload: Optional[Dict[str, Any]],
    error: Optional[str],
    *,
    force_delay_minutes: Optional[int] = None,
) -> None:
    ensure_db()
    now = utc_now()
    now_s = dt_to_iso(now)

    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT interval_minutes FROM watchlist WHERE key=?;", (key,)).fetchone()
        if not row:
            return

        interval = int(row["interval_minutes"])

        if force_delay_minutes is not None:
            next_run = now + timedelta(minutes=int(force_delay_minutes))
        else:
            next_run = now + timedelta(minutes=interval)

        next_run_s = dt_to_iso(next_run)

        payload_s = json.dumps(payload, ensure_ascii=False) if payload is not None else None

        con.execute(
            """
            UPDATE watchlist
            SET last_run_utc=?,
                last_status=?,
                last_error=?,
                last_payload_json=?,
                next_run_utc=?
            WHERE key=?;
            """,
            (now_s, status_code, error, payload_s, next_run_s, key),
        )
