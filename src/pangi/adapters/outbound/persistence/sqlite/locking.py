"""Non-blocking process lock for the single-process SQLite profile."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType

from pangi.adapters.outbound.persistence.sqlite.errors import (
    StorageBusyError,
    StorageSafetyError,
)

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class ProcessFileLock:
    """Own an advisory lock for one Pangi data directory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        if self._path.is_symlink() or (self._path.exists() and not self._path.is_file()):
            raise StorageSafetyError("process lock target is unsafe")
        descriptor = os.open(
            self._path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | _NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{os.getpid()}\n".encode())
            os.fsync(descriptor)
        except BlockingIOError as error:
            os.close(descriptor)
            raise StorageBusyError(
                "SQLite storage is already owned by another Pangi process"
            ) from error
        except OSError:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            self._descriptor = None

    def __enter__(self) -> ProcessFileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def process_lock_available(path: Path) -> bool:
    """Probe an existing lock file without creating or modifying it."""

    if not path.exists():
        return not path.is_symlink()
    if path.is_symlink() or not path.is_file():
        raise StorageSafetyError("process lock target is unsafe")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | _NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)
