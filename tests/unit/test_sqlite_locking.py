"""Single-process file lock tests."""

from pathlib import Path

import pytest

from pangi.adapters.outbound.persistence.sqlite.errors import StorageBusyError
from pangi.adapters.outbound.persistence.sqlite.locking import (
    ProcessFileLock,
    process_lock_available,
)


def test_second_lock_is_rejected_and_probe_is_read_only(tmp_path: Path) -> None:
    lock_path = tmp_path / "pangi.lock"
    assert process_lock_available(lock_path)
    assert not lock_path.exists()

    with ProcessFileLock(lock_path):
        assert not process_lock_available(lock_path)
        with pytest.raises(StorageBusyError, match="another Pangi process"):
            ProcessFileLock(lock_path).acquire()

    assert process_lock_available(lock_path)
    assert lock_path.read_text("utf-8").strip().isdigit()
