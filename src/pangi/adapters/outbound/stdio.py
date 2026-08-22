"""Fail-closed stdio launch resolution without starting an external process."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from pangi.application.contracts.secrets import SecretMaterial
from pangi.application.contracts.stdio import (
    ResolvedStdioLaunch,
    StdioConnectionNotSupportedError,
    StdioEnvironmentNotAllowedError,
    StdioEnvironmentSecretError,
    StdioExecutableNotAllowedError,
    StdioExecutableUnsafeError,
    StdioWorkingDirectoryUnsafeError,
    is_reserved_environment_name,
)
from pangi.application.ports.secrets import SecretStore, SecretStoreError
from pangi.config import StdioMcpConfig
from pangi.domain.connections import Connection, ConnectionTransport


class StdioLaunchResolver:
    """Resolve registered launch input and retain identities for pre-spawn revalidation."""

    def __init__(
        self,
        *,
        data_dir: Path,
        config: StdioMcpConfig,
        secret_store: SecretStore,
    ) -> None:
        if not data_dir.is_absolute():
            raise ValueError("data_dir must be absolute")
        self._data_dir = data_dir.absolute()
        self._config = config
        self._secret_store = secret_store

    async def resolve(self, connection: Connection) -> ResolvedStdioLaunch:
        """Return validated material only after every referenced Secret resolves."""

        if (
            not isinstance(connection, Connection)
            or connection.transport is not ConnectionTransport.STDIO
            or connection.command is None
        ):
            raise StdioConnectionNotSupportedError

        executable, executable_identity = await asyncio.to_thread(
            self._resolve_executable,
            connection.command,
        )
        environment = await self._resolve_environment(connection)
        working_directory, directory_identity = await asyncio.to_thread(
            self._resolve_working_directory,
            connection.id,
        )
        return ResolvedStdioLaunch(
            executable=executable,
            args=connection.args,
            environment=environment,
            working_directory=working_directory,
            executable_device=executable_identity[0],
            executable_inode=executable_identity[1],
            working_directory_device=directory_identity[0],
            working_directory_inode=directory_identity[1],
        )

    async def revalidate(self, launch: ResolvedStdioLaunch) -> None:
        """Fail if the executable or working directory changed after resolution."""

        if not isinstance(launch, ResolvedStdioLaunch):
            raise TypeError("launch must be a ResolvedStdioLaunch")
        await asyncio.to_thread(self._revalidate_filesystem, launch)

    def _resolve_executable(self, command: str) -> tuple[Path, tuple[int, int]]:
        configured_path: str | None
        candidate = Path(command)
        if candidate.is_absolute():
            configured_path = (
                command if command in self._config.allowed_executables else None
            )
        else:
            configured_path = self._config.executable_aliases.get(command)
        if configured_path is None:
            raise StdioExecutableNotAllowedError

        path, metadata = self._secure_executable(Path(configured_path))
        return path, (metadata.st_dev, metadata.st_ino)

    @staticmethod
    def _secure_executable(configured: Path) -> tuple[Path, os.stat_result]:
        try:
            canonical = configured.resolve(strict=True)
            metadata = os.lstat(canonical)
        except (OSError, RuntimeError):
            raise StdioExecutableUnsafeError from None
        if (
            not canonical.is_absolute()
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o111 == 0
            or stat.S_IMODE(metadata.st_mode) & 0o022 != 0
        ):
            raise StdioExecutableUnsafeError
        getuid = getattr(os, "getuid", None)
        if getuid is not None and metadata.st_uid not in {0, getuid()}:
            raise StdioExecutableUnsafeError
        return canonical, metadata

    async def _resolve_environment(self, connection: Connection) -> dict[str, str]:
        allowed = frozenset(self._config.environment_allowlist)
        references = connection.env_secret_refs
        if any(
            name not in allowed or is_reserved_environment_name(name)
            for name in references
        ):
            raise StdioEnvironmentNotAllowedError

        resolved: dict[str, str] = {}
        for name, reference in references.items():
            try:
                material = await self._secret_store.get(reference)
            except SecretStoreError:
                raise StdioEnvironmentSecretError from None
            except Exception:
                raise StdioEnvironmentSecretError from None
            if not isinstance(material, SecretMaterial) or "\x00" in material.value:
                raise StdioEnvironmentSecretError
            resolved[name] = material.value
        return resolved

    def _resolve_working_directory(self, connection_id: str) -> tuple[Path, tuple[int, int]]:
        data_metadata = self._secure_existing_directory(
            self._data_dir,
            exact_mode=None,
            error_type=StdioWorkingDirectoryUnsafeError,
        )
        connectors = self._data_dir / "connectors"
        connectors_metadata = self._ensure_directory(connectors)
        current_data = self._secure_existing_directory(
            self._data_dir,
            exact_mode=None,
            error_type=StdioWorkingDirectoryUnsafeError,
        )
        if (data_metadata.st_dev, data_metadata.st_ino) != (
            current_data.st_dev,
            current_data.st_ino,
        ):
            raise StdioWorkingDirectoryUnsafeError

        working_directory = connectors / connection_id
        directory_metadata = self._ensure_directory(working_directory)
        current_connectors = self._secure_existing_directory(
            connectors,
            exact_mode=0o700,
            error_type=StdioWorkingDirectoryUnsafeError,
        )
        if (connectors_metadata.st_dev, connectors_metadata.st_ino) != (
            current_connectors.st_dev,
            current_connectors.st_ino,
        ):
            raise StdioWorkingDirectoryUnsafeError
        current_directory = self._secure_existing_directory(
            working_directory,
            exact_mode=0o700,
            error_type=StdioWorkingDirectoryUnsafeError,
        )
        if (directory_metadata.st_dev, directory_metadata.st_ino) != (
            current_directory.st_dev,
            current_directory.st_ino,
        ):
            raise StdioWorkingDirectoryUnsafeError
        try:
            if working_directory.resolve(strict=True).parent != connectors.resolve(strict=True):
                raise StdioWorkingDirectoryUnsafeError
        except (OSError, RuntimeError):
            raise StdioWorkingDirectoryUnsafeError from None
        return working_directory, (directory_metadata.st_dev, directory_metadata.st_ino)

    @staticmethod
    def _ensure_directory(path: Path) -> os.stat_result:
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError:
            pass
        except (OSError, RuntimeError):
            raise StdioWorkingDirectoryUnsafeError from None
        return StdioLaunchResolver._secure_existing_directory(
            path,
            exact_mode=0o700,
            error_type=StdioWorkingDirectoryUnsafeError,
        )

    @staticmethod
    def _secure_existing_directory(
        path: Path,
        *,
        exact_mode: int | None,
        error_type: type[StdioWorkingDirectoryUnsafeError],
    ) -> os.stat_result:
        try:
            metadata = os.lstat(path)
            canonical = path.resolve(strict=True)
        except OSError:
            raise error_type from None
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            canonical != path.absolute()
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (exact_mode is not None and mode != exact_mode)
            or (exact_mode is None and mode & 0o022 != 0)
        ):
            raise error_type
        getuid = getattr(os, "getuid", None)
        if getuid is not None and metadata.st_uid != getuid():
            raise error_type
        return metadata

    def _revalidate_filesystem(self, launch: ResolvedStdioLaunch) -> None:
        _, executable = self._secure_executable(launch.executable)
        if (executable.st_dev, executable.st_ino) != (
            launch.executable_device,
            launch.executable_inode,
        ):
            raise StdioExecutableUnsafeError
        connectors = launch.working_directory.parent
        self._secure_existing_directory(
            connectors,
            exact_mode=0o700,
            error_type=StdioWorkingDirectoryUnsafeError,
        )
        directory = self._secure_existing_directory(
            launch.working_directory,
            exact_mode=0o700,
            error_type=StdioWorkingDirectoryUnsafeError,
        )
        if (directory.st_dev, directory.st_ino) != (
            launch.working_directory_device,
            launch.working_directory_inode,
        ):
            raise StdioWorkingDirectoryUnsafeError
