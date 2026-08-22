"""SQLite issuance and atomic single-use consumption for Tool approvals."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.audit import SqliteAuditWriter
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.audit import AuditEventDraft
from pangi.application.contracts.tool_approval_persistence import (
    IssuedToolApproval,
    ToolApprovalConsumption,
    ToolApprovalExpectation,
    ToolApprovalIssuancePolicy,
    ToolApprovalIssueCommand,
)
from pangi.application.contracts.tool_guardrails import ApprovalGrant
from pangi.application.ports.tool_approval_persistence import (
    ToolApprovalConflictError,
    ToolApprovalIssueDeniedError,
    ToolApprovalPersistenceError,
)
from pangi.application.services.audit import core_audit_redaction_service
from pangi.domain.audit import AuditOutcome
from pangi.domain.auth import UserRole
from pangi.domain.tool_guardrails import ToolApprovalRequirement

IdFactory = Callable[[], str]
ReferenceFactory = Callable[[], str]

_APPROVAL_COLUMNS = """
    id,
    subject_user_id,
    approver_user_id,
    approver_role,
    run_id,
    stable_tool_id,
    arguments_fingerprint,
    policy_fingerprint,
    approval_requirement,
    state,
    issued_at,
    expires_at,
    consumed_at
"""


def _identifier() -> str:
    return uuid.uuid4().hex


def _reference() -> str:
    return secrets.token_urlsafe(32)


def _reference_hash(reference: str) -> str:
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def _valid_reference(reference: str) -> bool:
    return (
        32 <= len(reference) <= 1_024
        and all(0x21 <= ord(character) <= 0x7E for character in reference)
    )


def _grant_from_row(row: aiosqlite.Row) -> ApprovalGrant:
    return ApprovalGrant(
        grant_id=str(row["id"]),
        subject_user_id=str(row["subject_user_id"]),
        approver_user_id=str(row["approver_user_id"]),
        approver_role=UserRole(str(row["approver_role"])),
        approval_requirement=ToolApprovalRequirement(
            str(row["approval_requirement"])
        ),
        run_id=str(row["run_id"]),
        tool_id=str(row["stable_tool_id"]),
        arguments_fingerprint=str(row["arguments_fingerprint"]),
        policy_fingerprint=str(row["policy_fingerprint"]),
        expires_at=datetime.fromisoformat(str(row["expires_at"])),
    )


class SqliteToolApprovalStore:
    """Persist hashed references and consume exact grants in one transaction."""

    def __init__(
        self,
        database: SqliteDatabase,
        policy: ToolApprovalIssuancePolicy,
        audit_writer: SqliteAuditWriter | None = None,
        *,
        id_factory: IdFactory = _identifier,
        reference_factory: ReferenceFactory = _reference,
    ) -> None:
        self._database = database
        self._policy = policy
        self._audit_writer = audit_writer or SqliteAuditWriter(
            core_audit_redaction_service()
        )
        self._id_factory = id_factory
        self._reference_factory = reference_factory

    @asynccontextmanager
    async def _runtime(self) -> AsyncIterator[None]:
        started_here = not self._database.started
        if started_here:
            await self._database.start()
        try:
            yield
        finally:
            if started_here:
                await self._database.close()

    async def issue_grant(
        self,
        command: ToolApprovalIssueCommand,
    ) -> IssuedToolApproval:
        if not isinstance(command, ToolApprovalIssueCommand):
            raise TypeError("command must be a ToolApprovalIssueCommand")
        ttl_seconds = (command.expires_at - command.issued_at).total_seconds()
        if ttl_seconds > self._policy.max_ttl_seconds:
            raise ToolApprovalIssueDeniedError("Tool Approval expiry exceeds policy")
        grant_id = self._id_factory()
        reference = self._reference_factory()
        if not _valid_reference(reference):
            raise ToolApprovalPersistenceError(
                "Generated Tool Approval reference is invalid"
            )
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = unit_of_work.connection
                current = await self._current_context(
                    connection,
                    subject_user_id=command.subject_user_id,
                    approver_user_id=command.approver_user_id,
                    run_id=command.run_id,
                    tool_id=command.tool_id,
                )
                if not self._can_issue(command, current):
                    raise ToolApprovalIssueDeniedError(
                        "Current state cannot issue the Tool Approval"
                    )
                await connection.execute(
                    "INSERT INTO tool_approvals "
                    "(id, reference_hash, subject_user_id, approver_user_id, "
                    "approver_role, run_id, stable_tool_id, arguments_fingerprint, "
                    "policy_fingerprint, approval_requirement, state, issued_at, "
                    "expires_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "'active', ?, ?, NULL)",
                    (
                        grant_id,
                        _reference_hash(reference),
                        command.subject_user_id,
                        command.approver_user_id,
                        command.approver_role.value,
                        command.run_id,
                        command.tool_id,
                        command.arguments_fingerprint,
                        command.policy_fingerprint,
                        command.approval_requirement.value,
                        command.issued_at.isoformat(),
                        command.expires_at.isoformat(),
                    ),
                )
                await self._audit_writer.insert(
                    connection,
                    AuditEventDraft(
                        actor_id=command.approver_user_id,
                        action="tool_approval.grant_issued",
                        resource_type="tool_approval",
                        resource_id=grant_id,
                        outcome=AuditOutcome.SUCCEEDED,
                        created_at=command.issued_at,
                        before_summary=None,
                        after_summary={
                            "approval_requirement": command.approval_requirement.value,
                            "expires_at": command.expires_at.isoformat(),
                            "state": "active",
                        },
                        details={
                            "arguments_fingerprint": command.arguments_fingerprint,
                            "policy_fingerprint": command.policy_fingerprint,
                            "tool_id": command.tool_id,
                        },
                    ),
                )
                await unit_of_work.commit()
        except ToolApprovalIssueDeniedError:
            raise
        except aiosqlite.IntegrityError as error:
            raise ToolApprovalConflictError(
                "Tool Approval issuance violated a persistence constraint"
            ) from error
        except aiosqlite.Error as error:
            raise ToolApprovalPersistenceError(
                "Tool Approval could not be issued"
            ) from error
        grant = ApprovalGrant(
            grant_id=grant_id,
            subject_user_id=command.subject_user_id,
            approver_user_id=command.approver_user_id,
            approver_role=command.approver_role,
            approval_requirement=command.approval_requirement,
            run_id=command.run_id,
            tool_id=command.tool_id,
            arguments_fingerprint=command.arguments_fingerprint,
            policy_fingerprint=command.policy_fingerprint,
            expires_at=command.expires_at,
        )
        return IssuedToolApproval(reference, grant)

    async def consume_approval(
        self,
        approval_reference: str,
        *,
        expectation: ToolApprovalExpectation,
    ) -> ToolApprovalConsumption:
        if not isinstance(expectation, ToolApprovalExpectation):
            raise TypeError("expectation must be a ToolApprovalExpectation")
        if not _valid_reference(approval_reference):
            return ToolApprovalConsumption.invalid()
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = unit_of_work.connection
                row = await fetch_one(
                    connection,
                    f"SELECT {_APPROVAL_COLUMNS} FROM tool_approvals "
                    "WHERE reference_hash = ?",
                    (_reference_hash(approval_reference),),
                )
                if row is None or str(row["state"]) != "active":
                    await unit_of_work.commit()
                    return ToolApprovalConsumption.invalid()
                expires_at = datetime.fromisoformat(str(row["expires_at"])).astimezone(
                    UTC
                )
                if expires_at <= expectation.consumed_at:
                    await unit_of_work.commit()
                    return ToolApprovalConsumption.expired()
                if not self._claims_match(row, expectation):
                    await unit_of_work.commit()
                    return ToolApprovalConsumption.invalid()
                current = await self._current_context(
                    connection,
                    subject_user_id=str(row["subject_user_id"]),
                    approver_user_id=str(row["approver_user_id"]),
                    run_id=str(row["run_id"]),
                    tool_id=str(row["stable_tool_id"]),
                )
                if not self._can_consume(row, expectation, current):
                    await unit_of_work.commit()
                    return ToolApprovalConsumption.invalid()
                cursor = await connection.execute(
                    "UPDATE tool_approvals SET state = 'consumed', consumed_at = ? "
                    "WHERE id = ? AND state = 'active' AND consumed_at IS NULL",
                    (expectation.consumed_at.isoformat(), str(row["id"])),
                )
                try:
                    if cursor.rowcount != 1:
                        await unit_of_work.rollback()
                        return ToolApprovalConsumption.invalid()
                finally:
                    await cursor.close()
                await self._audit_writer.insert(
                    connection,
                    AuditEventDraft(
                        actor_id=expectation.subject_user_id,
                        action="tool_approval.grant_consumed",
                        resource_type="tool_approval",
                        resource_id=str(row["id"]),
                        outcome=AuditOutcome.SUCCEEDED,
                        created_at=expectation.consumed_at,
                        before_summary={"state": "active"},
                        after_summary={"state": "consumed"},
                        details={
                            "approval_requirement": expectation.approval_requirement.value,
                            "arguments_fingerprint": expectation.arguments_fingerprint,
                            "policy_fingerprint": expectation.policy_fingerprint,
                            "tool_id": expectation.tool_id,
                        },
                    ),
                )
                await unit_of_work.commit()
                return ToolApprovalConsumption.consumed(_grant_from_row(row))
        except (KeyError, TypeError, ValueError) as error:
            raise ToolApprovalPersistenceError(
                "Persisted Tool Approval data is invalid"
            ) from error
        except aiosqlite.Error as error:
            raise ToolApprovalPersistenceError(
                "Tool Approval could not be consumed"
            ) from error

    @staticmethod
    async def _current_context(
        connection: aiosqlite.Connection,
        *,
        subject_user_id: str,
        approver_user_id: str,
        run_id: str,
        tool_id: str,
    ) -> aiosqlite.Row | None:
        return await fetch_one(
            connection,
            "SELECT subject.status AS subject_status, "
            "approver.status AS approver_status, approver.role AS current_approver_role, "
            "r.principal_id AS run_principal_id, ct.state AS tool_state, "
            "c.state AS connection_state, tp.effect AS policy_effect, "
            "tp.approval AS policy_approval, tp.policy_fingerprint "
            "FROM runs r "
            "JOIN users subject ON subject.id = ? "
            "JOIN users approver ON approver.id = ? "
            "JOIN connection_tools ct ON ct.stable_tool_id = ? "
            "JOIN connections c ON c.id = ct.connection_id "
            "LEFT JOIN tool_policies tp ON tp.stable_tool_id = ct.stable_tool_id "
            "AND tp.state = 'active' WHERE r.id = ?",
            (subject_user_id, approver_user_id, tool_id, run_id),
        )

    @staticmethod
    def _can_issue(
        command: ToolApprovalIssueCommand,
        current: aiosqlite.Row | None,
    ) -> bool:
        if current is None:
            return False
        role = str(current["current_approver_role"])
        if (
            str(current["subject_status"]) != "active"
            or str(current["approver_status"]) != "active"
            or str(current["run_principal_id"]) != command.subject_user_id
            or str(current["tool_state"]) != "active"
            or str(current["connection_state"]) != "connected"
            or str(current["policy_effect"]) != "allow"
            or str(current["policy_approval"])
            != command.approval_requirement.value
            or str(current["policy_fingerprint"]) != command.policy_fingerprint
            or role != command.approver_role.value
        ):
            return False
        if command.approval_requirement is ToolApprovalRequirement.USER:
            return command.approver_user_id == command.subject_user_id
        return role == UserRole.ADMIN.value

    @staticmethod
    def _claims_match(
        row: aiosqlite.Row,
        expectation: ToolApprovalExpectation,
    ) -> bool:
        return (
            str(row["subject_user_id"]) == expectation.subject_user_id
            and str(row["run_id"]) == expectation.run_id
            and str(row["stable_tool_id"]) == expectation.tool_id
            and str(row["arguments_fingerprint"])
            == expectation.arguments_fingerprint
            and str(row["policy_fingerprint"]) == expectation.policy_fingerprint
            and str(row["approval_requirement"])
            == expectation.approval_requirement.value
        )

    @staticmethod
    def _can_consume(
        row: aiosqlite.Row,
        expectation: ToolApprovalExpectation,
        current: aiosqlite.Row | None,
    ) -> bool:
        if current is None:
            return False
        role = str(current["current_approver_role"])
        if (
            str(current["subject_status"]) != "active"
            or str(current["approver_status"]) != "active"
            or str(current["run_principal_id"]) != expectation.subject_user_id
            or str(current["tool_state"]) != "active"
            or str(current["connection_state"]) != "connected"
            or str(current["policy_effect"]) != "allow"
            or str(current["policy_approval"])
            != expectation.approval_requirement.value
            or str(current["policy_fingerprint"])
            != expectation.policy_fingerprint
        ):
            return False
        if expectation.approval_requirement is ToolApprovalRequirement.USER:
            return str(row["approver_user_id"]) == expectation.subject_user_id
        return role == UserRole.ADMIN.value
