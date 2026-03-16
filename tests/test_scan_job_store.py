from __future__ import annotations
import time
import pytest
from app.scan_job_store import create_job, finish_job, fail_job, get_job, _jobs, _JOB_TTL_S

def test_create_and_finish_job(tmp_path):
    jid = create_job(10)
    assert get_job(jid)["status"] == "pending"
    finish_job(jid, accepted=[{"isbn":"x"}]*3, rejected=[], stats={"total_isbns":10})
    j = get_job(jid)
    assert j["status"] == "done"
    assert len(j["accepted"]) == 3

def test_fail_job():
    jid = create_job(5)
    fail_job(jid, "network error")
    assert get_job(jid)["status"] == "error"
    assert get_job(jid)["error"] == "network error"

def test_eviction_removes_old_completed_jobs(monkeypatch):
    # Create a done job with old timestamp
    jid = create_job(1)
    finish_job(jid, [], [], {})
    _jobs[jid]["created_at"] = time.time() - _JOB_TTL_S - 1

    # Create a new job — eviction runs at create_job time
    new_jid = create_job(1)
    assert get_job(jid) is None, "Old done job should be evicted"
    assert get_job(new_jid) is not None, "New job should exist"

def test_eviction_keeps_running_jobs(monkeypatch):
    jid = create_job(1)
    _jobs[jid]["created_at"] = time.time() - _JOB_TTL_S - 1
    # Status is still "pending" (running) — should NOT be evicted
    new_jid = create_job(1)
    assert get_job(jid) is not None, "Running job must not be evicted"
