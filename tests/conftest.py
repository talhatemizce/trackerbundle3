from __future__ import annotations
import asyncio, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_global_state(monkeypatch, tmp_path):
    from app import ai_analyst, scan_job_store
    ai_analyst._ai_cache.clear()
    ai_analyst._ai_inflight.clear()
    scan_job_store._jobs.clear()
    data_dir = tmp_path / "scan_data"
    monkeypatch.setattr(scan_job_store, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(scan_job_store, "HISTORY_FILE", data_dir / "scan_history.json", raising=False)
    try:
        import app.main as main
        main._ai_requests.clear()
    except Exception:
        pass
    yield
    ai_analyst._ai_cache.clear()
    ai_analyst._ai_inflight.clear()
    scan_job_store._jobs.clear()
