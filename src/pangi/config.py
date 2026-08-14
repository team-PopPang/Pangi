"""Typed public configuration facade."""

from __future__ import annotations

import json
import re
import tomllib
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


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


class StorageConfig(_StrictModel):
    """Local SQLite profile supported by the 1.0 runtime."""

    url: Literal["sqlite:///{data_dir}/pangi.sqlite3"] = "sqlite:///{data_dir}/pangi.sqlite3"
    journal_mode: Literal["delete"] = "delete"
    busy_timeout_ms: int = Field(default=5000, ge=100, le=60000)


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
    storage: StorageConfig = Field(default_factory=StorageConfig)
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
                "",
                "[storage]",
                f"url = {quote(self.storage.url)}",
                f"journal_mode = {quote(self.storage.journal_mode)}",
                f"busy_timeout_ms = {self.storage.busy_timeout_ms}",
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
