"""AWS Bedrock Converse adapter loaded only when explicitly selected."""

from __future__ import annotations

import asyncio
import importlib
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


class BedrockConverseClient(Protocol):
    def converse(self, **kwargs: object) -> object:
        """Execute one synchronous Bedrock Converse request."""

        ...


def _mapping(value: object) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Bedrock response shape is invalid")
    return value


def _get(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _token_usage(response: object) -> ModelTokenUsage | None:
    usage = _get(response, "usage")
    input_tokens = _integer(_get(usage, "inputTokens"))
    output_tokens = _integer(_get(usage, "outputTokens"))
    total_tokens = _integer(_get(usage, "totalTokens"))
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _finish_reason(response: object) -> ModelFinishReason:
    reason = _get(response, "stopReason")
    if reason in {"end_turn", "stop_sequence"}:
        return ModelFinishReason.STOP
    if reason in {"max_tokens", "model_context_window_exceeded"}:
        return ModelFinishReason.LENGTH
    if reason in {"guardrail_intervened", "content_filtered"}:
        return ModelFinishReason.CONTENT_FILTERED
    return ModelFinishReason.UNKNOWN


def _status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    metadata = _get(response, "ResponseMetadata")
    return _integer(_get(metadata, "HTTPStatusCode"))


def _normalize_failure(error: Exception) -> ModelProviderFailure:
    status = _status_code(error)
    name = type(error).__name__.lower()
    if status in {401, 403} or "accessdenied" in name or "unrecognizedclient" in name:
        return ModelProviderFailure(ModelProviderErrorCode.AUTHENTICATION, retryable=False)
    if status == 408 or "timeout" in name:
        return ModelProviderFailure(ModelProviderErrorCode.TIMEOUT, retryable=True)
    if status == 429 or "throttl" in name or "modelnotready" in name:
        return ModelProviderFailure(ModelProviderErrorCode.RATE_LIMITED, retryable=True)
    if status in {500, 502, 503, 504} or "serviceunavailable" in name:
        return ModelProviderFailure(ModelProviderErrorCode.UNAVAILABLE, retryable=True)
    if isinstance(status, int) and 400 <= status <= 499:
        return ModelProviderFailure(ModelProviderErrorCode.INVALID_REQUEST, retryable=False)
    if "connection" in name:
        return ModelProviderFailure(ModelProviderErrorCode.UNAVAILABLE, retryable=True)
    return ModelProviderFailure(ModelProviderErrorCode.UNKNOWN, retryable=False)


def _output_text(response: object) -> str:
    output = _mapping(_get(response, "output"))
    message = _mapping(output.get("message"))
    content = message.get("content")
    if not isinstance(content, list):
        raise ValueError("Bedrock output content is invalid")
    texts: list[str] = []
    for block in content:
        text = _get(block, "text")
        if isinstance(text, str):
            texts.append(text)
    rendered = "".join(texts)
    if not rendered.strip():
        raise ValueError("Bedrock structured output is missing")
    return rendered


class BedrockModelProvider:
    """Map one guarded Pangi request to one Bedrock Network Request."""

    def __init__(self, client: BedrockConverseClient) -> None:
        self._client = client

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        system, user = split_rendered_sources(request)
        kwargs: dict[str, object] = {
            "messages": [
                {"content": [{"text": content}], "role": "user"} for content in user
            ],
            "modelId": request.profile.model,
            "outputConfig": {
                "textFormat": {
                    "structure": {
                        "jsonSchema": {
                            "description": "Pangi structured Model output",
                            "name": request.output_schema.name,
                            "schema": request.output_schema.canonical_schema_json,
                        }
                    },
                    "type": "json_schema",
                }
            },
        }
        if system:
            kwargs["system"] = [{"text": content} for content in system]
        try:
            response = await asyncio.to_thread(self._client.converse, **kwargs)
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
            metrics = _get(response, "metrics")
            return ModelProviderResponse(
                canonical_output_json=_output_text(response),
                token_usage=_token_usage(response),
                provider_latency_ms=_integer(_get(metrics, "latencyMs")),
                finish_reason=finish_reason,
            )
        except ModelProviderFailure:
            raise
        except (TypeError, ValueError):
            raise ModelProviderFailure(
                ModelProviderErrorCode.INVALID_STRUCTURED_OUTPUT,
                retryable=False,
            ) from None


def create_bedrock_provider(
    *,
    region_name: str,
    retry_policy: ProviderRetryPolicy,
) -> RetryingModelProvider:
    """Create a Bedrock adapter with Boto3 retries disabled."""

    if not region_name.strip():
        raise ValueError("Bedrock region name cannot be blank")
    try:
        boto3 = importlib.import_module("boto3")
        botocore_config = importlib.import_module("botocore.config")
    except ModuleNotFoundError:
        raise OptionalModelProviderDependencyError("bedrock") from None
    client_value = getattr(boto3, "client", None)
    config_value = getattr(botocore_config, "Config", None)
    if not callable(client_value) or not callable(config_value):
        raise OptionalModelProviderDependencyError("bedrock")
    client_factory = cast(Callable[..., object], client_value)
    config_factory = cast(Callable[..., object], config_value)
    config = config_factory(
        connect_timeout=min(10.0, retry_policy.attempt_timeout_seconds),
        read_timeout=retry_policy.attempt_timeout_seconds,
        retries={"mode": "standard", "total_max_attempts": 1},
    )
    client = cast(
        BedrockConverseClient,
        client_factory("bedrock-runtime", region_name=region_name, config=config),
    )
    return RetryingModelProvider(BedrockModelProvider(client), retry_policy)
