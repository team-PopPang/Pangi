"""Run creation, idempotency, cursor, and owner-scope use cases."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.runs import (
    RunCreateRecord,
    RunCreation,
    RunCursorPosition,
    RunListPage,
    RunListQuery,
    RunStoreQuery,
)
from pangi.application.ports.runs import (
    InvalidRunCursorError,
    RunNotFoundError,
    RunStore,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import EventVisibility, Run, RunEvent, RunRequest, RunState

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]

_ROUTE_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_CURSOR_VERSION = 1
_DEFAULT_IDEMPOTENCY_TTL = timedelta(hours=24)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier() -> str:
    return uuid.uuid4().hex


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def request_fingerprint(request: RunRequest) -> str:
    """Hash semantic request fields while excluding transport retry metadata."""

    attachments = [
        {
            "display_name": attachment.display_name,
            "fingerprint": attachment.fingerprint,
            "media_type": attachment.media_type,
            "reference": attachment.reference,
            "size_bytes": attachment.size_bytes,
        }
        for attachment in request.attachments
    ]
    semantic_request = {
        "attachments": attachments,
        "channel": request.principal.channel.value,
        "explicit_skill": request.explicit_skill,
        "schedule_id": request.schedule_id,
        "text": request.text,
        "thread_key": request.thread_key,
    }
    return hashlib.sha256(_canonical_json(semantic_request).encode("utf-8")).hexdigest()


def _query_fingerprint(
    actor: AuthenticatedPrincipal,
    query: RunListQuery,
    *,
    owner_user_id: str | None,
) -> str:
    scope = {
        "actor_role": actor.role.value,
        "actor_user_id": actor.user_id,
        "owner_user_id": owner_user_id,
        "states": sorted(state.value for state in query.states),
        "triggers": sorted(trigger.value for trigger in query.triggers),
    }
    return hashlib.sha256(_canonical_json(scope).encode("utf-8")).hexdigest()


def _encode_cursor(position: RunCursorPosition, *, query_fingerprint: str) -> str:
    payload = _canonical_json(
        {
            "created_at": position.created_at.astimezone(UTC).isoformat(),
            "query_fingerprint": query_fingerprint,
            "run_id": position.run_id,
            "version": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, *, query_fingerprint: str) -> RunCursorPosition:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "created_at",
            "query_fingerprint",
            "run_id",
            "version",
        }:
            raise ValueError
        if payload["version"] != _CURSOR_VERSION:
            raise ValueError
        if payload["query_fingerprint"] != query_fingerprint:
            raise ValueError
        created_at_value = payload["created_at"]
        run_id = payload["run_id"]
        if not isinstance(created_at_value, str) or not isinstance(run_id, str):
            raise ValueError
        if not 16 <= len(run_id) <= 64 or run_id.strip() != run_id:
            raise ValueError
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InvalidRunCursorError("The Run cursor is invalid") from error
    return RunCursorPosition(created_at.astimezone(UTC), run_id)


class RunService:
    """Coordinate safe Run persistence without depending on a concrete database."""

    def __init__(
        self,
        store: RunStore,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _identifier,
        idempotency_ttl: timedelta = _DEFAULT_IDEMPOTENCY_TTL,
    ) -> None:
        if idempotency_ttl <= timedelta(0):
            raise ValueError("idempotency_ttl must be positive")
        self._store = store
        self._clock = clock
        self._id_factory = id_factory
        self._idempotency_ttl = idempotency_ttl

    async def create_run(self, request: RunRequest, *, route_key: str) -> RunCreation:
        if _ROUTE_KEY.fullmatch(route_key) is None:
            raise ValueError("route_key must contain 1-120 lowercase namespace characters")
        recorded_at = self._clock().astimezone(UTC)
        run = Run(
            id=self._id_factory(),
            request=request,
            state=RunState.RECEIVED,
            updated_at=request.created_at,
        )
        first_event = RunEvent(
            run_id=run.id,
            index=1,
            type="run.received",
            visibility=EventVisibility.PUBLIC,
            created_at=request.created_at,
            message="Request received",
            attributes={"trigger": request.principal.channel.value},
        )
        return await self._store.create_or_replay(
            RunCreateRecord(
                run=run,
                first_event=first_event,
                route_key=route_key,
                request_fingerprint=request_fingerprint(request),
                recorded_at=recorded_at,
                expires_at=recorded_at + self._idempotency_ttl,
            )
        )

    async def get_run(self, *, actor: AuthenticatedPrincipal, run_id: str) -> Run:
        owner_user_id = self._owner_user_id(actor)
        run = await self._store.get_run(run_id=run_id, owner_user_id=owner_user_id)
        if run is None:
            raise RunNotFoundError("The Run was not found")
        return run

    async def list_runs(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: RunListQuery,
    ) -> RunListPage:
        owner_user_id = self._owner_user_id(actor)
        fingerprint = _query_fingerprint(actor, query, owner_user_id=owner_user_id)
        after = (
            _decode_cursor(query.cursor, query_fingerprint=fingerprint)
            if query.cursor is not None
            else None
        )
        fetched = await self._store.list_run_summaries(
            RunStoreQuery(
                owner_user_id=owner_user_id,
                states=query.states,
                triggers=query.triggers,
                limit=query.limit + 1,
                after=after,
            )
        )
        items = fetched[: query.limit]
        next_cursor = None
        if len(fetched) > query.limit and items:
            last = items[-1]
            next_cursor = _encode_cursor(
                RunCursorPosition(last.created_at, last.id),
                query_fingerprint=fingerprint,
            )
        return RunListPage(items, next_cursor)

    @staticmethod
    def _owner_user_id(actor: AuthenticatedPrincipal) -> str | None:
        if actor.status is not UserStatus.ACTIVE:
            raise RunNotFoundError("The Run was not found")
        return None if actor.role is UserRole.ADMIN else actor.user_id
