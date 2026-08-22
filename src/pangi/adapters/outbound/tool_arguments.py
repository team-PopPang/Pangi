"""Network-free JSON Schema validation for canonical Tool arguments."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import cast

from pangi.application.contracts.tool_guardrails import ResolvedTool
from pangi.application.ports.connections import ConnectionRegistryStore
from pangi.domain.connections import ConnectionToolState

_LOCAL_REFERENCE_KEYS = frozenset({"$dynamicRef", "$recursiveRef", "$ref"})


class OptionalToolArgumentDependencyError(RuntimeError):
    """The MCP argument validation dependency is unavailable."""

    code = "tool_argument_dependency_unavailable"


class JsonSchemaToolArgumentValidator:
    """Validate against an exact Registry snapshot without resolving remote references."""

    def __init__(
        self,
        registry: ConnectionRegistryStore,
        *,
        max_cached_schemas: int = 128,
    ) -> None:
        if max_cached_schemas < 1:
            raise ValueError("max_cached_schemas must be positive")
        try:
            module = importlib.import_module("jsonschema")
        except ModuleNotFoundError:
            raise OptionalToolArgumentDependencyError from None
        validator_type = getattr(module, "Draft202012Validator", None)
        check_schema = getattr(validator_type, "check_schema", None)
        if not callable(validator_type) or not callable(check_schema):
            raise OptionalToolArgumentDependencyError
        self._registry = registry
        self._validator_factory = cast(Callable[[object], object], validator_type)
        self._check_schema = cast(Callable[[object], None], check_schema)
        self._max_cached_schemas = max_cached_schemas
        self._validators: OrderedDict[str, Callable[[object], bool]] = OrderedDict()

    async def validate_arguments(
        self,
        *,
        tool: ResolvedTool,
        canonical_arguments_json: str,
    ) -> bool:
        try:
            snapshot = await self._registry.get_tool_snapshot(tool.tool_id)
            if snapshot is None or (
                snapshot.state is not ConnectionToolState.ACTIVE
                or snapshot.connection_id != tool.connection_id
                or snapshot.connection_scope is not tool.connection_scope
                or snapshot.connection_owner_user_id != tool.connection_owner_user_id
                or snapshot.permission is not tool.permission
                or snapshot.schema_fingerprint != tool.schema_fingerprint
            ):
                return False
            arguments = json.loads(canonical_arguments_json)
            if not isinstance(arguments, dict):
                return False
            if (
                json.dumps(
                    arguments,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                != canonical_arguments_json
            ):
                return False
            schema = json.loads(snapshot.canonical_schema_json)
            if not isinstance(schema, dict) or not _references_are_local(schema):
                return False
            validator = self._validators.get(snapshot.schema_fingerprint)
            if validator is None:
                self._check_schema(schema)
                instance = self._validator_factory(schema)
                is_valid = getattr(instance, "is_valid", None)
                if not callable(is_valid):
                    return False
                validator = cast(Callable[[object], bool], is_valid)
                self._validators[snapshot.schema_fingerprint] = validator
                self._validators.move_to_end(snapshot.schema_fingerprint)
                while len(self._validators) > self._max_cached_schemas:
                    self._validators.popitem(last=False)
            else:
                self._validators.move_to_end(snapshot.schema_fingerprint)
            return bool(await asyncio.to_thread(validator, arguments))
        except Exception:
            return False


def _references_are_local(value: object) -> bool:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if key in _LOCAL_REFERENCE_KEYS:
                    if not isinstance(nested, str) or not (
                        nested == "#" or nested.startswith("#/")
                    ):
                        return False
                stack.append(nested)
        elif isinstance(item, Sequence) and not isinstance(item, str):
            stack.extend(item)
    return True
