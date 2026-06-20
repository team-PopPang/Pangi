"""Storage repository interfaces and implementations."""

from pangi.repository.job_repository_protocol import DEFAULT_REPO_KEY, JobRepository
from pangi.repository.job_repository_sqlite_impl import (
    DuplicateEventError,
    SQLiteJobRepository,
    get_job_repository,
    set_job_repository,
)

__all__ = [
    "DEFAULT_REPO_KEY",
    "DuplicateEventError",
    "JobRepository",
    "SQLiteJobRepository",
    "get_job_repository",
    "set_job_repository",
]
