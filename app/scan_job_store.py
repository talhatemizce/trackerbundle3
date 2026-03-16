"""
Scan Job Store — background CSV arb taramaları için in-memory job tracker.
Her job: {id, status, progress, total, accepted, rejected, stats, error, created_at}
"""
from __future__ import annotations
import time, uuid, asyncio, json
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR = Path(__file__).resolve().parent / "data"
HISTORY_FILE = DATA_DIR / "scan_history.json"

# ── In-memory job store ───────────────────────────────────────────────────────
_jobs: Dict[str, Dict] = {}  # job_id → job dict

def create_job(total: int) -> str:
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "id": job_id,
        "status": "pending",   # pending → running → done | error
        "progress": 0,
        "total": total,
        "accepted": [],
        "rejected": [],
        "stats": {},
        "error": None,
        "created_at": time.time(),
        "eta_s": None,
        "started_at": None,
    }
    return job_id

def update_progress(job_id: str, done: int) -> None:
    job = _jobs.get(job_id)
    if not job: return
    job["progress"] = done
    if job["started_at"] and done > 0:
        elapsed = time.time() - job["started_at"]
        rate = done / elapsed  # ISBN/s
        remaining = job["total"] - done
        job["eta_s"] = round(remaining / rate) if rate > 0 else None

def finish_job(job_id: str, accepted: list, rejected: list, stats: dict) -> None:
    job = _jobs.get(job_id)
    if not job: return
    job["status"] = "done"
    job["progress"] = job["total"]
    job["accepted"] = accepted
    job["rejected"] = rejected
    job["stats"] = stats
    _save_to_history(job_id, accepted, rejected, stats)

def fail_job(job_id: str, error: str) -> None:
    job = _jobs.get(job_id)
    if not job: return
    job["status"] = "error"
    job["error"] = error

def get_job(job_id: str) -> Optional[Dict]:
    return _jobs.get(job_id)

def get_job_progress(job_id: str) -> Optional[Dict]:
    """Poll endpoint için — heavy lists olmadan sadece progress."""
    job = _jobs.get(job_id)
    if not job: return None
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "eta_s": job["eta_s"],
        "error": job["error"],
        "accepted_count": len(job["accepted"]),
        "rejected_count": len(job["rejected"]),
        # Done olunca küçük preview (ilk 5)
        "preview": job["accepted"][:5] if job["status"] == "done" else [],
    }

# ── Scan history (disk) ───────────────────────────────────────────────────────

def _save_to_history(job_id: str, accepted: list, rejected: list, stats: dict) -> None:
    try:
        DATA_DIR.mkdir(exist_ok=True)
        existing = []
        if HISTORY_FILE.exists():
            try:
                existing = json.loads(HISTORY_FILE.read_text())
            except Exception:
                existing = []
        entry = {
            "job_id": job_id,
            "ts": time.time(),
            "stats": stats,
            "accepted": accepted[:200],   # max 200 accepted kaydet
            "rejected_count": len(rejected),
            "top_reasons": _top_reasons(rejected),
        }
        existing.insert(0, entry)
        existing = existing[:50]  # max 50 scan
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        tmp.replace(HISTORY_FILE)
    except Exception as e:
        import logging
        logging.getLogger("trackerbundle.scan_history").warning("save_history failed: %s", e)

def _top_reasons(rejected: list) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in rejected:
        reason = r.get("reason") or "unknown"
        # sadece temel neden (parantez öncesi)
        key = reason.split("(")[0].split("_below")[0].split("_above")[0]
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1])[:8])

def get_history() -> list:
    try:
        if HISTORY_FILE.exists():
            return json.loads(HISTORY_FILE.read_text())
    except Exception:
        pass
    return []
