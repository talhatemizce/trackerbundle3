from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """
    OS-level exclusive file lock (sync).
    async içinde await yapılmayan yerlerde güvenle kullanılabilir.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _read_unsafe(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Lock OLMADAN oku. Sadece file_lock bloğu içinden çağır."""
    if default is None:
        default = {}
    if not path.exists():
        return dict(default)
    try:
        txt = path.read_text(encoding="utf-8").strip()
        if not txt:
            return dict(default)
        return json.loads(txt)
    except Exception:
        return dict(default)


def _write_unsafe(path: Path, data: Dict[str, Any]) -> None:
    """Atomic write, lock OLMADAN. Sadece file_lock bloğu içinden çağır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)  # atomic
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with file_lock(path):
        return _read_unsafe(path, default)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with file_lock(path):
        _write_unsafe(path, data)
