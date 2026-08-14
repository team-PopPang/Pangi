"""Safe SQLite storage errors suitable for CLI boundaries."""

from pangi.application.ports.storage import StorageOperationError


class StorageError(StorageOperationError):
    """Base error for expected storage failures."""


class StorageSafetyError(StorageError):
    """The selected storage path or filesystem is unsafe."""


class StorageBusyError(StorageError):
    """Another Pangi process owns the storage lock."""


class MigrationError(StorageError):
    """Base error for migration planning or application failures."""


class MigrationIntegrityError(MigrationError):
    """Applied migration history does not match the package resources."""


class MigrationApplyError(MigrationError):
    """A pending migration batch failed and was rolled back."""
