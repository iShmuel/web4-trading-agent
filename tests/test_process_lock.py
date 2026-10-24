"""Tests for the single-instance process lock that prevents duplicate bots."""

import subprocess
import sys
import textwrap

import pytest

from core.process_lock import SingleInstanceLock, AlreadyRunningError, is_locked, read_lock_pid


def test_lock_records_pid_and_releases(tmp_path):
    lock_path = tmp_path / "bot.lock"
    with SingleInstanceLock(lock_path):
        assert is_locked(lock_path)
        assert read_lock_pid(lock_path) is not None
    assert not is_locked(lock_path)


def test_second_lock_in_same_process_is_rejected(tmp_path):
    lock_path = tmp_path / "bot.lock"
    with SingleInstanceLock(lock_path):
        with pytest.raises(AlreadyRunningError):
            SingleInstanceLock(lock_path).acquire()


def test_second_process_is_rejected_while_first_holds_lock(tmp_path):
    """A separate OS process (like a second terminal) must be refused."""
    lock_path = tmp_path / "bot.lock"
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[1])!r})
        from core.process_lock import SingleInstanceLock, AlreadyRunningError
        try:
            SingleInstanceLock({str(lock_path)!r}).acquire()
        except AlreadyRunningError:
            sys.exit(3)
        sys.exit(0)
    """)
    with SingleInstanceLock(lock_path):
        blocked = subprocess.run([sys.executable, "-c", script])
        assert blocked.returncode == 3

    # Once the first process releases, a new one can start.
    allowed = subprocess.run([sys.executable, "-c", script])
    assert allowed.returncode == 0
