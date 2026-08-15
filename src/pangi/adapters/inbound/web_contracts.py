"""Explicit HTTP schemas for the Pangi Admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pangi.application.contracts.auth import AuthenticatedPrincipal, SessionView
from pangi.application.contracts.bootstrap import BootstrapAdminResult
from pangi.domain.auth import UserRole, UserStatus


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BootstrapAdminRequest(_StrictApiModel):
    """One-time credentials used to create the first local administrator."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    token: str = Field(min_length=20, max_length=256, json_schema_extra={"writeOnly": True})
    local_id: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(
        min_length=12,
        max_length=256,
        json_schema_extra={"writeOnly": True},
    )


class LoginRequest(_StrictApiModel):
    """Local administrator credentials."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    local_id: str = Field(min_length=1, max_length=80)
    password: str = Field(
        min_length=1,
        max_length=256,
        json_schema_extra={"writeOnly": True},
    )


class AdminResponse(_StrictApiModel):
    user_id: str
    local_id: str
    display_name: str
    role: Literal["admin"]
    status: Literal["active"]

    @classmethod
    def from_contract(cls, result: BootstrapAdminResult) -> AdminResponse:
        return cls(
            user_id=result.user_id,
            local_id=result.local_id,
            display_name=result.display_name,
            role="admin",
            status="active",
        )


class BootstrapAdminResponse(_StrictApiModel):
    admin: AdminResponse

    @classmethod
    def from_contract(cls, result: BootstrapAdminResult) -> BootstrapAdminResponse:
        return cls(admin=AdminResponse.from_contract(result))


class PrincipalResponse(_StrictApiModel):
    user_id: str
    display_name: str
    role: UserRole
    status: UserStatus

    @classmethod
    def from_contract(cls, principal: AuthenticatedPrincipal) -> PrincipalResponse:
        return cls(
            user_id=principal.user_id,
            display_name=principal.display_name,
            role=principal.role,
            status=principal.status,
        )


class SessionResponse(_StrictApiModel):
    principal: PrincipalResponse
    expires_at: datetime
    rotation_due_at: datetime
    rotation_due: bool

    @classmethod
    def from_contract(cls, session: SessionView) -> SessionResponse:
        return cls(
            principal=PrincipalResponse.from_contract(session.principal),
            expires_at=session.expires_at,
            rotation_due_at=session.rotation_due_at,
            rotation_due=session.rotation_due,
        )


class SessionEnvelope(_StrictApiModel):
    session: SessionResponse

    @classmethod
    def from_contract(cls, session: SessionView) -> SessionEnvelope:
        return cls(session=SessionResponse.from_contract(session))


class ErrorBody(_StrictApiModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(_StrictApiModel):
    error: ErrorBody

    @classmethod
    def create(cls, *, code: str, message: str, request_id: str) -> ErrorEnvelope:
        return cls(error=ErrorBody(code=code, message=message, request_id=request_id))
