"""OpenAI Responses API adapter loaded only when explicitly selected."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from typing import Protocol, cast

from pangi.adapters.outbound.model_providers.common import (
    OptionalModelProviderDependencyError,
    split_rendered_sources,
)
from pangi.adapters.outbound.model_providers.retry import RetryingModelProvider
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelProviderFailure,
    ModelProviderResponse,
    ModelTokenUsage,
    ProviderRetryPolicy,
)
from pangi.domain.model_routing import (
    ModelFinishReason,
    ModelProviderErrorCode,
)


class OpenAIResponsesAPI(Protocol):
    async def create(self, **kwargs: object) -> object:
        """Create one non-streaming Responses API request."""

        ...


class OpenAIClient(Protocol):
    responses: OpenAIResponsesAPI


def _read(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _token_usage(response: object) -> ModelTokenUsage | None:
    usage = _read(response, "usage")
    input_tokens = _integer(_read(usage, "input_tokens"))
    output_tokens = _integer(_read(usage, "output_tokens"))
    total_tokens = _integer(_read(usage, "total_tokens"))
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _finish_reason(response: object) -> ModelFinishReason:
    status = _read(response, "status")
    if status == "completed":
        return ModelFinishReason.STOP
    if status == "incomplete":
        details = _read(response, "incomplete_details")
        reason = _read(details, "reason")
        if reason == "max_output_tokens":
            return ModelFinishReason.LENGTH
        if reason == "content_filter":
            return ModelFinishReason.CONTENT_FILTERED
    return ModelFinishReason.UNKNOWN


def _normalize_failure(error: Exception) -> ModelProviderFailure:
    status = _read(error, "status_code")
    name = type(error).__name__.lower()
    if status in {401, 403}:
        return ModelProviderFailure(ModelProviderErrorCode.AUTHENTICATION, retryable=False)
    if status in {408} or "timeout" in name:
        return ModelProviderFailure(ModelProviderErrorCode.TIMEOUT, retryable=True)
    if status == 429:
        return ModelProviderFailure(ModelProviderErrorCode.RATE_LIMITED, retryable=True)
    if isinstance(status, int) and 500 <= status <= 599:
        return ModelProviderFailure(ModelProviderErrorCode.UNAVAILABLE, retryable=True)
    if isinstance(status, int) and 400 <= status <= 499:
        return ModelProviderFailure(ModelProviderErrorCode.INVALID_REQUEST, retryable=False)
    if "connection" in name:
        return ModelProviderFailure(ModelProviderErrorCode.UNAVAILABLE, retryable=True)
    return ModelProviderFailure(ModelProviderErrorCode.UNKNOWN, retryable=False)


class OpenAIModelProvider:
    """Map one guarded Pangi request to one OpenAI Network Request."""

    def __init__(self, client: OpenAIClient) -> None:
        self._client = client

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        system, user = split_rendered_sources(request)
        kwargs: dict[str, object] = {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                }
                for content in user
            ],
            "model": request.profile.model,
            "store": False,
            "text": {
                "format": {
                    "name": request.output_schema.name,
                    "schema": json.loads(request.output_schema.canonical_schema_json),
                    "strict": True,
                    "type": "json_schema",
                }
            },
        }
        if system:
            kwargs["instructions"] = "\n".join(system)
        try:
            response = await self._client.responses.create(**kwargs)
        except Exception as error:
            raise _normalize_failure(error) from None

        try:
            finish_reason = _finish_reason(response)
            if finish_reason is not ModelFinishReason.STOP:
                code = (
                    ModelProviderErrorCode.CONTENT_FILTERED
                    if finish_reason is ModelFinishReason.CONTENT_FILTERED
                    else ModelProviderErrorCode.INVALID_STRUCTURED_OUTPUT
                )
                raise ModelProviderFailure(code, retryable=False)
            output_text = _read(response, "output_text")
            if not isinstance(output_text, str) or not output_text.strip():
                raise ValueError("missing structured output")
            return ModelProviderResponse(
                canonical_output_json=output_text,
                token_usage=_token_usage(response),
                finish_reason=finish_reason,
            )
        except ModelProviderFailure:
            raise
        except (TypeError, ValueError):
            raise ModelProviderFailure(
                ModelProviderErrorCode.INVALID_STRUCTURED_OUTPUT,
                retryable=False,
            ) from None


def create_openai_provider(
    *,
    retry_policy: ProviderRetryPolicy,
    api_key: str | None = None,
    base_url: str | None = None,
) -> RetryingModelProvider:
    """Create an OpenAI adapter with SDK retries disabled."""

    try:
        module = importlib.import_module("openai")
    except ModuleNotFoundError:
        raise OptionalModelProviderDependencyError("openai") from None
    client_value = getattr(module, "AsyncOpenAI", None)
    if not callable(client_value):
        raise OptionalModelProviderDependencyError("openai")
    client_type = cast(Callable[..., object], client_value)
    kwargs: dict[str, object] = {
        "max_retries": 0,
        "timeout": retry_policy.attempt_timeout_seconds,
    }
    if api_key is not None:
        if not api_key.strip():
            raise ValueError("OpenAI API key cannot be blank")
        kwargs["api_key"] = api_key
    if base_url is not None:
        if not base_url.strip():
            raise ValueError("OpenAI base URL cannot be blank")
        kwargs["base_url"] = base_url
    client = cast(OpenAIClient, client_type(**kwargs))
    return RetryingModelProvider(OpenAIModelProvider(client), retry_policy)
