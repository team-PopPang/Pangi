"""Fail-closed stdio launch policy tests without spawning a process."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pangi.adapters.outbound.stdio import StdioLaunchResolver
from pangi.application.contracts.secrets import (
    SecretBackend,
    SecretMaterial,
    SecretReference,
)
from pangi.application.contracts.stdio import (
    StdioEnvironmentNotAllowedError,
    StdioEnvironmentSecretError,
    StdioExecutableNotAllowedError,
    StdioExecutableUnsafeError,
    StdioWorkingDirectoryUnsafeError,
)
from pangi.application.ports.secrets import SecretNotFoundError
from pangi.config import StdioMcpConfig
from pangi.domain.connections import (
    Connection,
    ConnectionAuthType,
    ConnectionScope,
    ConnectionState,
    ConnectionTransport,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
REFERENCE = SecretReference(SecretBackend.FILE_VAULT, "environmenttoken01")


class FakeSecretStore:
    def __init__(
        self,
        values: dict[str, SecretMaterial] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.values = values or {}
        self.failure = failure
        self.requests: list[str] = []

    async def get(self, reference: SecretReference) -> SecretMaterial:
        self.requests.append(reference.value)
        if self.failure is not None:
            raise self.failure
        try:
            return self.values[reference.value]
        except KeyError:
            raise SecretNotFoundError from None


def _runtime(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(mode=0o700)
    executable = bin_dir / "filesystem-mcp"
    executable.write_text("#!/bin/sh\nexit 0\n", "utf-8")
    executable.chmod(0o700)
    return data_dir, executable


def _connection(command: str, **changes: object) -> Connection:
    values: dict[str, object] = {
        "id": "connection-instance-stdio",
        "kind": "filesystem",
        "display_name": "Filesystem",
        "scope": ConnectionScope.INSTANCE,
        "transport": ConnectionTransport.STDIO,
        "command": command,
        "args": ("--literal=$TOKEN;echo",),
        "env_secret_refs": {"FILESYSTEM_TOKEN": REFERENCE},
        "auth_type": ConnectionAuthType.ENVIRONMENT,
        "state": ConnectionState.DISCONNECTED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return Connection(**values)  # type: ignore[arg-type]


def _resolver(
    data_dir: Path,
    executable: Path,
    store: FakeSecretStore,
    *,
    alias: bool = False,
    allow_environment: bool = True,
) -> StdioLaunchResolver:
    config = StdioMcpConfig(
        allowed_executables=() if alias else (str(executable),),
        executable_aliases={"filesystem": str(executable)} if alias else {},
        environment_allowlist=("FILESYSTEM_TOKEN",) if allow_environment else (),
    )
    return StdioLaunchResolver(data_dir=data_dir, config=config, secret_store=store)


def test_resolve_preserves_literal_args_and_hides_sensitive_launch_material(
    tmp_path: Path,
) -> None:
    data_dir, executable = _runtime(tmp_path)
    secret = "secret-value-that-must-not-appear"
    store = FakeSecretStore({REFERENCE.value: SecretMaterial(secret)})
    resolver = _resolver(data_dir, executable, store)

    launch = asyncio.run(resolver.resolve(_connection(str(executable))))

    assert launch.executable == executable.resolve()
    assert launch.args == ("--literal=$TOKEN;echo",)
    assert launch.environment == {"FILESYSTEM_TOKEN": secret}
    assert launch.working_directory == data_dir / "connectors" / "connection-instance-stdio"
    assert launch.working_directory.stat().st_mode & 0o777 == 0o700
    assert store.requests == [REFERENCE.value]
    rendered = repr(launch)
    for sensitive in (
        str(executable),
        "$TOKEN;echo",
        "FILESYSTEM_TOKEN",
        secret,
        REFERENCE.value,
        str(launch.working_directory),
    ):
        assert sensitive not in rendered
    asyncio.run(resolver.revalidate(launch))


def test_alias_resolves_to_canonical_executable_without_path_search(tmp_path: Path) -> None:
    data_dir, executable = _runtime(tmp_path)
    link = executable.parent / "filesystem-link"
    link.symlink_to(executable)
    store = FakeSecretStore({REFERENCE.value: SecretMaterial("token")})
    resolver = _resolver(data_dir, link, store, alias=True)

    launch = asyncio.run(resolver.resolve(_connection("filesystem")))

    assert launch.executable == executable.resolve()

    direct_only = _resolver(data_dir, executable, store)
    with pytest.raises(StdioExecutableNotAllowedError):
        asyncio.run(direct_only.resolve(_connection("filesystem-mcp")))


def test_unregistered_or_unsafe_executable_is_rejected(tmp_path: Path) -> None:
    data_dir, executable = _runtime(tmp_path)
    store = FakeSecretStore({REFERENCE.value: SecretMaterial("token")})
    resolver = _resolver(data_dir, executable, store)
    other = executable.parent / "other-mcp"
    other.write_text("#!/bin/sh\n", "utf-8")
    other.chmod(0o700)

    with pytest.raises(StdioExecutableNotAllowedError):
        asyncio.run(resolver.resolve(_connection(str(other))))

    executable.chmod(0o722)
    with pytest.raises(StdioExecutableUnsafeError):
        asyncio.run(resolver.resolve(_connection(str(executable))))


def test_environment_allowlist_and_secret_failures_are_fail_closed(tmp_path: Path) -> None:
    data_dir, executable = _runtime(tmp_path)
    connection = _connection(str(executable))
    denied = _resolver(
        data_dir,
        executable,
        FakeSecretStore({REFERENCE.value: SecretMaterial("token")}),
        allow_environment=False,
    )

    with pytest.raises(StdioEnvironmentNotAllowedError):
        asyncio.run(denied.resolve(connection))
    assert not (data_dir / "connectors").exists()

    for store in (
        FakeSecretStore(),
        FakeSecretStore(failure=RuntimeError("backend detail must not escape")),
        FakeSecretStore({REFERENCE.value: SecretMaterial("contains\x00nul")}),
    ):
        resolver = _resolver(data_dir, executable, store)
        with pytest.raises(StdioEnvironmentSecretError) as captured:
            asyncio.run(resolver.resolve(connection))
        assert "backend detail" not in str(captured.value)
        assert REFERENCE.value not in str(captured.value)
        assert not (data_dir / "connectors").exists()


def test_working_directory_permissions_and_identity_replacement_are_rejected(
    tmp_path: Path,
) -> None:
    data_dir, executable = _runtime(tmp_path)
    store = FakeSecretStore({REFERENCE.value: SecretMaterial("token")})
    resolver = _resolver(data_dir, executable, store)
    connectors = data_dir / "connectors"
    connectors.mkdir(mode=0o755)

    with pytest.raises(StdioWorkingDirectoryUnsafeError):
        asyncio.run(resolver.resolve(_connection(str(executable))))

    connectors.chmod(0o700)
    launch = asyncio.run(resolver.resolve(_connection(str(executable))))
    launch.working_directory.rmdir()
    launch.working_directory.mkdir(mode=0o700)
    with pytest.raises(StdioWorkingDirectoryUnsafeError):
        asyncio.run(resolver.revalidate(launch))


def test_executable_identity_replacement_is_detected_before_spawn(tmp_path: Path) -> None:
    data_dir, executable = _runtime(tmp_path)
    store = FakeSecretStore({REFERENCE.value: SecretMaterial("token")})
    resolver = _resolver(data_dir, executable, store)
    launch = asyncio.run(resolver.resolve(_connection(str(executable))))
    replacement = executable.with_suffix(".replacement")
    replacement.write_text("#!/bin/sh\nexit 1\n", "utf-8")
    replacement.chmod(0o700)
    os.replace(replacement, executable)

    with pytest.raises(StdioExecutableUnsafeError):
        asyncio.run(resolver.revalidate(launch))
