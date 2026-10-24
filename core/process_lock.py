"""Single-instance process lock.

Prevents two trading processes from writing the same paper portfolio file at
the same time (a "split-brain" race where each process overwrites the other's
trades). Uses an OS-level advisory lock, which the kernel releases automatically
if the process crashes or is killed, so stale locks cannot block a restart.

Standard library only (fcntl on POSIX, msvcrt on Windows).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt
else:
    import fcntl


class AlreadyRunningError(RuntimeError):
    """Raised when another process already holds the lock."""

    def __init__(self, lock_path: Path, holder_pid: Optional[int]):
        self.lock_path = lock_path
        self.holder_pid = holder_pid
        who = f"PID {holder_pid}" if holder_pid else "another process"
        super().__init__(f"Another trading bot is already running ({who}). Lock file: {lock_path}")


def read_lock_pid(lock_path: Path) -> Optional[int]:
    """Return the PID recorded in the lock file, if any."""
    try:
        text = Path(lock_path).read_text().strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


class SingleInstanceLock:
    """Exclusive, non-blocking lock around a lock file.

    Usage:
        with SingleInstanceLock(path):
            run_bot()
    """

    def __init__(self, lock_path: Path, record_pid: bool = True):
        self.lock_path = Path(lock_path)
        self.record_pid = record_pid
        self._fh = None

    def acquire(self) -> "SingleInstanceLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+")
        try:
            if os.name == "nt":  # pragma: no cover
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise AlreadyRunningError(self.lock_path, read_lock_pid(self.lock_path))

        if self.record_pid:
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if self.record_pid:
                self._fh.seek(0)
                self._fh.truncate()
                self._fh.flush()
            if os.name == "nt":  # pragma: no cover
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def is_locked(lock_path: Path) -> bool:
    """Probe (read-only intent) whether some process currently holds the lock."""
    probe = SingleInstanceLock(lock_path, record_pid=False)
    try:
        probe.acquire()
    except AlreadyRunningError:
        return True
    probe.release()
    return False
