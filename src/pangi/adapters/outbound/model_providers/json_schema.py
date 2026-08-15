"""Optional JSON Schema validator for structured Model output."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from typing import cast

from pangi.adapters.outbound.model_providers.common import (
    OptionalModelProviderDependencyError,
)
from pangi.application.contracts.model_routing import StructuredOutputSchema


class JsonSchemaOutputValidator:
    """Validate parsed output without exposing schema or output in failures."""

    def __init__(self) -> None:
        try:
            module = importlib.import_module("jsonschema")
        except ModuleNotFoundError:
            raise OptionalModelProviderDependencyError("model") from None
        validate = getattr(module, "validate", None)
        if not callable(validate):
            raise OptionalModelProviderDependencyError("model")
        self._validate = cast(Callable[..., None], validate)

    def is_valid(
        self,
        *,
        schema: StructuredOutputSchema,
        canonical_output_json: str,
    ) -> bool:
        try:
            instance = json.loads(canonical_output_json)
            schema_value = json.loads(schema.canonical_schema_json)
            self._validate(instance=instance, schema=schema_value)
        except Exception:
            return False
        return True
