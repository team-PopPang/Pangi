"""SQLite filesystem safety policy tests."""

from pathlib import Path

import pytest

from pangi.adapters.outbound.persistence.sqlite.errors import StorageSafetyError
from pangi.adapters.outbound.persistence.sqlite.filesystem import (
    detect_filesystem_type,
    ensure_local_filesystem,
)


def test_known_network_filesystem_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(StorageSafetyError, match="network filesystem"):
        ensure_local_filesystem(tmp_path, "nfs")


def test_linux_mountinfo_uses_the_longest_matching_mount(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "20 1 0:1 / / rw - ext4 /dev/root rw\n"
        f"21 20 0:2 / {tmp_path} rw - nfs server:/volume rw\n",
        "utf-8",
    )

    detected = detect_filesystem_type(
        tmp_path / "data" / "pangi.sqlite3",
        platform="linux",
        mountinfo=mountinfo,
    )

    assert detected == "nfs"
