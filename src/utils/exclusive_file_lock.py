"""An exclusive, cross-process OS file lock held around a short critical section."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType

if os.name == "nt":  # pragma: no cover - exercised on the Windows CI runner
    import msvcrt
else:
    import fcntl

_DEFAULT_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 0.01


class ExclusiveFileLock:
    """Hold an exclusive OS lock on one file for the duration of a `with` block.

    POSIX uses `fcntl.flock`, Windows `msvcrt.locking` on the file's first byte. Each
    `with` opens its own handle, so two holders exclude each other whether they are
    processes or threads. The lock file's content is never written: only the OS lock
    carries meaning, and it is released when the block exits or the process dies.
    Acquisition polls without blocking, so a stuck holder surfaces as `TimeoutError`
    after `timeout` seconds instead of hanging the caller.
    """

    def __init__(self, path: str | Path, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Name the lock file (created if absent) and the acquisition deadline."""
        self._path = Path(path)
        self._timeout = timeout
        self._handle: int | None = None

    def __enter__(self) -> ExclusiveFileLock:
        """Acquire the lock, polling until it is free or the deadline passes."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                _try_lock(handle)
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(handle)
                    raise TimeoutError(
                        f"Lock '{self._path}' was not acquired within {self._timeout} s."
                    ) from None
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue
            self._handle = handle
            return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lock and close its handle."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            os.close(handle)


def _try_lock(handle: int) -> None:
    """Take the exclusive lock without blocking; `OSError` when another holder has it."""
    if os.name == "nt":  # pragma: no cover - exercised on the Windows CI runner
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: int) -> None:
    """Release the lock taken by `_try_lock`."""
    if os.name == "nt":  # pragma: no cover - exercised on the Windows CI runner
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)


__all__ = ["ExclusiveFileLock"]
