"""Typed public configuration facade."""

from __future__ import annotations

import json
import math
import re
import tomllib
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class PangiConfigError(ValueError):
    """A safe configuration loading or validation error."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class InstanceConfig(_StrictModel):
    """Human-facing instance metadata."""

    name: str = Field(default="pangi", min_length=1, max_length=80)
    timezone: str = "UTC"
    language: str = Field(default="ko", min_length=2, max_length=16)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class ServerConfig(_StrictModel):
    """Local server bind configuration."""

    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=8787, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            ip_address(value)
        except ValueError:
            labels = value.split(".")
            valid_hostname = len(value) <= 253 and all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels
            )
            if not valid_hostname:
                raise ValueError("host must be an IP address or DNS hostname") from None
        return value


class RuntimeConfig(_StrictModel):
    """Bounded runtime defaults used by later work packages."""

    max_concurrent_runs: int = Field(default=4, ge=1, le=64)
    max_subagents_per_run: int = Field(default=3, ge=0, le=16)
    run_timeout_seconds: int = Field(default=180, ge=1, le=3600)
    run_data_classes: tuple[
        Literal["public", "internal", "confidential", "personal", "restricted"],
        ...,
    ] = ("restricted",)

    @field_validator("run_data_classes", mode="before")
    @classmethod
    def normalize_run_data_classes(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("run_data_classes")
    @classmethod
    def validate_run_data_classes(
        cls,
        value: tuple[
            Literal["public", "internal", "confidential", "personal", "restricted"],
            ...,
        ],
    ) -> tuple[
        Literal["public", "internal", "confidential", "personal", "restricted"],
        ...,
    ]:
        if not value:
            raise ValueError("run_data_classes must contain at least one data class")
        if len(value) != len(set(value)):
            raise ValueError("run_data_classes cannot contain duplicates")
        return value


class ModelRuntimeConfig(_StrictModel):
    """Secret-free Root Model selection and Transport Retry limits."""

    root_profile: str = Field(
        default="root-default",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$",
    )
    max_attempts: int = Field(default=3, ge=1, le=10)
    attempt_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    total_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    retry_backoff_seconds: tuple[float, ...] = (0.5, 1.0)

    @field_validator("retry_backoff_seconds", mode="before")
    @classmethod
    def normalize_retry_backoff(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_retry_policy(self) -> ModelRuntimeConfig:
        if self.total_timeout_seconds < self.attempt_timeout_seconds:
            raise ValueError("total_timeout_seconds cannot be shorter than one attempt")
        if len(self.retry_backoff_seconds) != self.max_attempts - 1:
            raise ValueError("retry_backoff_seconds must define every retry delay")
        if any(
            not math.isfinite(value) or not 0 <= value <= 60
            for value in self.retry_backoff_seconds
        ):
            raise ValueError("retry backoff values must be finite and between 0 and 60")
        return self


class StorageConfig(_StrictModel):
    """Local SQLite profile supported by the 1.0 runtime."""

    url: Literal["sqlite:///{data_dir}/pangi.sqlite3"] = "sqlite:///{data_dir}/pangi.sqlite3"
    journal_mode: Literal["delete"] = "delete"
    busy_timeout_ms: int = Field(default=5000, ge=100, le=60000)


class SecretStoreConfig(_StrictModel):
    """Secret-free backend selection and external File Vault key location."""

    backend: Literal["auto", "keyring", "file-vault"] = "auto"
    master_key_source: Literal["environment", "file"] = "environment"
    master_key_environment_variable: str = Field(
        default="PANGI_SECRET_MASTER_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{0,127}$",
    )
    master_key_file: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_master_key_source(self) -> SecretStoreConfig:
        if self.master_key_source == "file":
            if self.master_key_file is None or not Path(self.master_key_file).is_absolute():
                raise ValueError("master_key_file must be an absolute path for the file source")
        elif self.master_key_file is not None:
            raise ValueError("master_key_file requires the file master key source")
        return self


class AuthConfig(_StrictModel):
    """Local first-run authentication settings."""

    bootstrap_grant_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    session_ttl_minutes: int = Field(default=720, ge=5, le=10080)
    session_rotation_minutes: int = Field(default=30, ge=5, le=1440)
    login_attempt_limit: int = Field(default=5, ge=1, le=50)
    login_attempt_window_seconds: int = Field(default=300, ge=60, le=3600)


class PangiConfig(_StrictModel):
    """Versioned configuration loaded from a strict TOML document."""

    schema_version: Literal[1] = 1
    instance: InstanceConfig = Field(default_factory=InstanceConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    model: ModelRuntimeConfig = Field(default_factory=ModelRuntimeConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    secrets: SecretStoreConfig = Field(default_factory=SecretStoreConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Load and validate a TOML configuration file."""

        config_path = Path(path)
        try:
            with config_path.open("rb") as config_file:
                values = tomllib.load(config_file)
        except FileNotFoundError as error:
            raise PangiConfigError(f"configuration file not found: {config_path}") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            message = f"configuration file could not be read: {config_path}"
            raise PangiConfigError(message) from error

        try:
            return cls.model_validate(values)
        except ValidationError as error:
            locations = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
            fields = ", ".join(sorted(set(locations))) or "document"
            raise PangiConfigError(f"configuration validation failed: {fields}") from error

    def to_toml(self) -> str:
        """Render a canonical secret-free configuration document."""

        def quote(value: str) -> str:
            return json.dumps(value, ensure_ascii=False)

        return "\n".join(
            (
                f"schema_version = {self.schema_version}",
                "",
                "[instance]",
                f"name = {quote(self.instance.name)}",
                f"timezone = {quote(self.instance.timezone)}",
                f"language = {quote(self.instance.language)}",
                "",
                "[server]",
                f"host = {quote(self.server.host)}",
                f"port = {self.server.port}",
                "",
                "[runtime]",
                f"max_concurrent_runs = {self.runtime.max_concurrent_runs}",
                f"max_subagents_per_run = {self.runtime.max_subagents_per_run}",
                f"run_timeout_seconds = {self.runtime.run_timeout_seconds}",
                "run_data_classes = ["
                + ", ".join(quote(value) for value in self.runtime.run_data_classes)
                + "]",
                "",
                "[model]",
                f"root_profile = {quote(self.model.root_profile)}",
                f"max_attempts = {self.model.max_attempts}",
                f"attempt_timeout_seconds = {self.model.attempt_timeout_seconds}",
                f"total_timeout_seconds = {self.model.total_timeout_seconds}",
                "retry_backoff_seconds = ["
                + ", ".join(str(value) for value in self.model.retry_backoff_seconds)
                + "]",
                "",
                "[storage]",
                f"url = {quote(self.storage.url)}",
                f"journal_mode = {quote(self.storage.journal_mode)}",
                f"busy_timeout_ms = {self.storage.busy_timeout_ms}",
                "",
                "[secrets]",
                f"backend = {quote(self.secrets.backend)}",
                f"master_key_source = {quote(self.secrets.master_key_source)}",
                "master_key_environment_variable = "
                + quote(self.secrets.master_key_environment_variable),
                *(
                    (f"master_key_file = {quote(self.secrets.master_key_file)}",)
                    if self.secrets.master_key_file is not None
                    else ()
                ),
                "",
                "[auth]",
                f"bootstrap_grant_ttl_minutes = {self.auth.bootstrap_grant_ttl_minutes}",
                f"session_ttl_minutes = {self.auth.session_ttl_minutes}",
                f"session_rotation_minutes = {self.auth.session_rotation_minutes}",
                f"login_attempt_limit = {self.auth.login_attempt_limit}",
                f"login_attempt_window_seconds = {self.auth.login_attempt_window_seconds}",
                "",
            )
        )
