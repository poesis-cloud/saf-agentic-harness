"""Unit tests for `ExclusiveFileLock` — the cross-process critical-section lock."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from utils.exclusive_file_lock import ExclusiveFileLock


class TestExclusiveFileLock:
    def test_a_second_holder_waits_until_the_first_releases(self, tmp_path: Path) -> None:
        """Spec function 0, invariant 5 (the critical section's mechanism): two holders
        of one lock file never overlap — the second enters only after the first exits."""
        path = tmp_path / "git" / "registration.lock"
        events: list[str] = []
        entered = threading.Event()

        def first() -> None:
            with ExclusiveFileLock(path):
                events.append("first-in")
                entered.set()
                time.sleep(0.2)
                events.append("first-out")

        def second() -> None:
            entered.wait(5)
            with ExclusiveFileLock(path):
                events.append("second-in")

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        assert events == ["first-in", "first-out", "second-in"]

    def test_a_held_lock_times_out_instead_of_hanging(self, tmp_path: Path) -> None:
        """A stuck holder surfaces as `TimeoutError` after the deadline, never a hang."""
        path = tmp_path / "registration.lock"
        with ExclusiveFileLock(path):
            with pytest.raises(TimeoutError):
                with ExclusiveFileLock(path, timeout=0.2):
                    pass

    def test_the_lock_is_reusable_once_released(self, tmp_path: Path) -> None:
        """Release frees the lock; the file's content is never written."""
        path = tmp_path / "registration.lock"
        with ExclusiveFileLock(path):
            pass
        with ExclusiveFileLock(path, timeout=0.5):
            pass

        assert path.read_bytes() == b""
