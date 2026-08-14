"""Supported-host filesystem classification for SQLite safety."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from pangi.adapters.outbound.persistence.sqlite.errors import StorageSafetyError

NETWORK_FILESYSTEMS = frozenset(
    {
        "9p",
        "afs",
        "ceph",
        "cifs",
        "fuse.sshfs",
        "glusterfs",
        "nfs",
        "nfs4",
        "smbfs",
        "sshfs",
    }
)
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mount_path(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _nearest_existing(path: Path) -> Path:
    candidate = path.absolute()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _linux_filesystem_type(path: Path, mountinfo: Path) -> str | None:
    try:
        lines = mountinfo.read_text("utf-8").splitlines()
    except OSError:
        return None

    target = os.fspath(_nearest_existing(path))
    matches: list[tuple[int, str]] = []
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_point = _unescape_mount_path(fields[4])
            filesystem_type = fields[separator + 1].lower()
        except (IndexError, ValueError):
            continue
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            matches.append((len(mount_point), filesystem_type))
    return max(matches, default=(0, ""))[1] or None


def _macos_filesystem_type(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["/sbin/mount"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    target = os.fspath(_nearest_existing(path))
    matches: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        try:
            location, options = line.rsplit(" (", 1)
            mount_point = _unescape_mount_path(location.split(" on ", 1)[1])
            filesystem_type = options.rstrip(")").split(",", 1)[0].lower()
        except (IndexError, ValueError):
            continue
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            matches.append((len(mount_point), filesystem_type))
    return max(matches, default=(0, ""))[1] or None


def detect_filesystem_type(
    path: Path,
    *,
    platform: str | None = None,
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> str | None:
    """Return the filesystem type without writing to the selected path."""

    current_platform = sys.platform if platform is None else platform
    if current_platform.startswith("linux"):
        return _linux_filesystem_type(path, mountinfo)
    if current_platform == "darwin":
        return _macos_filesystem_type(path)
    return None


def ensure_local_filesystem(path: Path, filesystem_type: str | None = None) -> str | None:
    """Reject filesystem profiles SQLite cannot safely support."""

    detected = detect_filesystem_type(path) if filesystem_type is None else filesystem_type
    normalized = detected.lower() if detected else None
    if normalized in NETWORK_FILESYSTEMS:
        raise StorageSafetyError(f"network filesystem is not supported for SQLite: {normalized}")
    return normalized
