"""
scan_job_store.py için genişletilmiş testler.
Önceki testlerden FARKLI: update_progress, append_result, get_job_progress,
pause/resume/cancel job, _top_reasons, get_history, status transitions.
"""
from __future__ import annotations
import json
import time
import pytest
from app.scan_job_store import (
    create_job, finish_job, fail_job, get_job, get_job_progress,
    update_progress, append_result,
    pause_job, resume_job, cancel_job,
    get_pause_event, get_cancel_event,
    get_history, _top_reasons, _jobs, _JOB_TTL_S,
)


# ── create_job() ──────────────────────────────────────────────────────────────

class TestCreateJob:
    def test_returns_string_id(self):
        jid = create_job(10)
        assert isinstance(jid, str)
        assert len(jid) == 8

    def test_initial_status_pending(self):
        jid = create_job(5)
        assert get_job(jid)["status"] == "pending"

    def test_initial_progress_zero(self):
        jid = create_job(10)
        assert get_job(jid)["progress"] == 0

    def test_total_stored(self):
        jid = create_job(42)
        assert get_job(jid)["total"] == 42

    def test_accepted_list_empty(self):
        jid = create_job(5)
        assert get_job(jid)["accepted"] == []

    def test_rejected_list_empty(self):
        jid = create_job(5)
        assert get_job(jid)["rejected"] == []

    def test_partial_accepted_list_empty(self):
        jid = create_job(5)
        assert get_job(jid)["partial_accepted"] == []

    def test_error_is_none(self):
        jid = create_job(5)
        assert get_job(jid)["error"] is None

    def test_created_at_recent(self):
        before = time.time()
        jid = create_job(1)
        after = time.time()
        ts = get_job(jid)["created_at"]
        assert before <= ts <= after

    def test_multiple_jobs_unique_ids(self):
        ids = [create_job(1) for _ in range(20)]
        assert len(set(ids)) == 20


# ── update_progress() ─────────────────────────────────────────────────────────

class TestUpdateProgress:
    def test_progress_updated(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        _jobs[jid]["started_at"] = time.time() - 5  # 5 seconds ago
        update_progress(jid, 5)
        assert get_job(jid)["progress"] == 5

    def test_nonexistent_job_no_crash(self):
        update_progress("nonexistent", 5)  # should not raise

    def test_eta_calculated_when_started(self):
        jid = create_job(10)
        _jobs[jid]["started_at"] = time.time() - 2  # 2 seconds for 2 ISBNs
        update_progress(jid, 2)
        job = get_job(jid)
        # eta_s = (10-2) / (2/2) = 8
        assert job["eta_s"] is not None
        assert job["eta_s"] > 0

    def test_eta_none_when_not_started(self):
        jid = create_job(10)
        update_progress(jid, 3)
        job = get_job(jid)
        # started_at is None → eta_s stays None
        assert job["eta_s"] is None

    def test_progress_to_total(self):
        jid = create_job(5)
        _jobs[jid]["started_at"] = time.time() - 5
        update_progress(jid, 5)
        assert get_job(jid)["progress"] == 5


# ── append_result() ───────────────────────────────────────────────────────────

class TestAppendResult:
    def test_accepted_appended(self):
        jid = create_job(5)
        append_result(jid, [{"isbn": "x1"}], [])
        assert len(get_job(jid)["partial_accepted"]) == 1

    def test_rejected_appended(self):
        jid = create_job(5)
        append_result(jid, [], [{"isbn": "x1", "reason": "roi_below"}])
        assert len(get_job(jid)["partial_rejected"]) == 1

    def test_multiple_appends_accumulate(self):
        jid = create_job(10)
        append_result(jid, [{"isbn": "x1"}], [])
        append_result(jid, [{"isbn": "x2"}], [])
        assert len(get_job(jid)["partial_accepted"]) == 2

    def test_nonexistent_job_no_crash(self):
        append_result("nonexistent", [{"isbn": "x"}], [])  # should not raise

    def test_mixed_append(self):
        jid = create_job(10)
        append_result(jid, [{"isbn": "a"}], [{"isbn": "b", "reason": "low"}])
        append_result(jid, [], [{"isbn": "c", "reason": "loss"}])
        assert len(get_job(jid)["partial_accepted"]) == 1
        assert len(get_job(jid)["partial_rejected"]) == 2


# ── get_job_progress() ────────────────────────────────────────────────────────

class TestGetJobProgress:
    def test_returns_none_for_nonexistent(self):
        assert get_job_progress("nonexistent") is None

    def test_progress_fields_present(self):
        jid = create_job(10)
        p = get_job_progress(jid)
        for key in ("id","status","paused","progress","total","eta_s","error",
                    "accepted_count","rejected_count","accepted","rejected","preview"):
            assert key in p, f"Missing key: {key}"

    def test_paused_field_false_for_pending(self):
        jid = create_job(10)
        p = get_job_progress(jid)
        assert p["paused"] is False

    def test_paused_field_true_when_paused(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        pause_job(jid)
        p = get_job_progress(jid)
        assert p["paused"] is True

    def test_running_uses_partial_results(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        append_result(jid, [{"isbn": "x"}], [])
        p = get_job_progress(jid)
        assert p["accepted_count"] == 1

    def test_done_uses_final_results(self):
        jid = create_job(2)
        finish_job(jid, accepted=[{"isbn": "a"}, {"isbn": "b"}], rejected=[], stats={})
        p = get_job_progress(jid)
        assert p["accepted_count"] == 2

    def test_preview_at_most_5_items(self):
        jid = create_job(20)
        _jobs[jid]["status"] = "running"
        for i in range(10):
            append_result(jid, [{"isbn": f"x{i}"}], [])
        p = get_job_progress(jid)
        assert len(p["preview"]) <= 5

    def test_rejected_capped_at_50(self):
        jid = create_job(200)
        items = [{"isbn": f"r{i}", "reason": "low"} for i in range(100)]
        _jobs[jid]["partial_rejected"] = items
        p = get_job_progress(jid)
        assert len(p["rejected"]) <= 50


# ── pause_job() / resume_job() / cancel_job() ─────────────────────────────────

class TestJobControls:
    def test_pause_running_job(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        result = pause_job(jid)
        assert result is True
        assert get_job(jid)["status"] == "paused"

    def test_pause_returns_false_for_done(self):
        jid = create_job(5)
        finish_job(jid, [], [], {})
        result = pause_job(jid)
        assert result is False

    def test_pause_returns_false_for_error(self):
        jid = create_job(5)
        fail_job(jid, "some error")
        result = pause_job(jid)
        assert result is False

    def test_resume_paused_job(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        pause_job(jid)
        result = resume_job(jid)
        assert result is True
        assert get_job(jid)["status"] == "running"

    def test_resume_returns_false_for_running(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        result = resume_job(jid)
        assert result is False

    def test_cancel_running_job(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        result = cancel_job(jid)
        assert result is True
        assert get_job(jid)["status"] == "cancelled"

    def test_cancel_pending_job(self):
        jid = create_job(10)
        result = cancel_job(jid)
        assert result is True
        assert get_job(jid)["status"] == "cancelled"

    def test_cancel_paused_job(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        pause_job(jid)
        result = cancel_job(jid)
        assert result is True

    def test_cancel_done_returns_false(self):
        jid = create_job(5)
        finish_job(jid, [], [], {})
        result = cancel_job(jid)
        assert result is False

    def test_cancel_nonexistent_returns_false(self):
        result = cancel_job("nonexistent")
        assert result is False

    def test_pause_event_set_when_paused(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        pause_job(jid)
        event = get_pause_event(jid)
        assert event.is_set()

    def test_pause_event_cleared_after_resume(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        pause_job(jid)
        resume_job(jid)
        event = get_pause_event(jid)
        assert not event.is_set()

    def test_cancel_event_set_after_cancel(self):
        jid = create_job(10)
        _jobs[jid]["status"] = "running"
        cancel_job(jid)
        event = get_cancel_event(jid)
        assert event.is_set()


# ── _top_reasons() ────────────────────────────────────────────────────────────

class TestTopReasons:
    def test_empty_list_returns_empty(self):
        assert _top_reasons([]) == {}

    def test_single_reason(self):
        rejected = [{"reason": "roi_below(10%)"}, {"reason": "roi_below(5%)"}]
        result = _top_reasons(rejected)
        assert "roi" in result
        assert result["roi"] == 2

    def test_unknown_reason_for_missing_key(self):
        rejected = [{"isbn": "x"}]  # no "reason" key
        result = _top_reasons(rejected)
        assert "unknown" in result

    def test_top_8_limit(self):
        reasons = [f"reason_{i}" for i in range(20)]
        rejected = [{"reason": r} for r in reasons]
        result = _top_reasons(rejected)
        assert len(result) <= 8

    def test_sorted_by_count_descending(self):
        rejected = [
            {"reason": "roi_below"}, {"reason": "roi_below"},
            {"reason": "loss"}, {"reason": "loss"}, {"reason": "loss"},
            {"reason": "other"},
        ]
        result = _top_reasons(rejected)
        values = list(result.values())
        assert values == sorted(values, reverse=True)


# ── get_history() / finish_job() disk persistence ─────────────────────────────

class TestHistoryPersistence:
    def test_get_history_empty_initially(self, tmp_path, monkeypatch):
        import app.scan_job_store as store
        monkeypatch.setattr(store, "DATA_DIR", tmp_path)
        monkeypatch.setattr(store, "HISTORY_FILE", tmp_path / "scan_history.json")
        assert store.get_history() == []

    def test_finish_job_saves_history(self, tmp_path, monkeypatch):
        import app.scan_job_store as store
        monkeypatch.setattr(store, "DATA_DIR", tmp_path)
        monkeypatch.setattr(store, "HISTORY_FILE", tmp_path / "scan_history.json")

        jid = create_job(3)
        finish_job(jid, accepted=[{"isbn": "x"}], rejected=[], stats={"total": 3})

        hist = store.get_history()
        assert len(hist) >= 1
        assert hist[0]["job_id"] == jid

    def test_history_contains_stats(self, tmp_path, monkeypatch):
        import app.scan_job_store as store
        monkeypatch.setattr(store, "DATA_DIR", tmp_path)
        monkeypatch.setattr(store, "HISTORY_FILE", tmp_path / "scan_history.json")

        jid = create_job(5)
        finish_job(jid, [], [], stats={"total_isbns": 5, "accepted_count": 0})
        hist = store.get_history()
        assert "stats" in hist[0]

    def test_history_max_50_scans(self, tmp_path, monkeypatch):
        import app.scan_job_store as store
        monkeypatch.setattr(store, "DATA_DIR", tmp_path)
        monkeypatch.setattr(store, "HISTORY_FILE", tmp_path / "scan_history.json")

        for _ in range(60):
            jid = create_job(1)
            finish_job(jid, [], [], {})

        hist = store.get_history()
        assert len(hist) <= 50

    def test_history_accepted_capped_at_200(self, tmp_path, monkeypatch):
        import app.scan_job_store as store
        monkeypatch.setattr(store, "DATA_DIR", tmp_path)
        monkeypatch.setattr(store, "HISTORY_FILE", tmp_path / "scan_history.json")

        jid = create_job(300)
        accepted = [{"isbn": f"x{i}"} for i in range(300)]
        finish_job(jid, accepted, [], {})

        hist = store.get_history()
        assert len(hist[0]["accepted"]) <= 200
