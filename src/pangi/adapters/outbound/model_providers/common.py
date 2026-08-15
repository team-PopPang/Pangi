"""Provider-neutral helpers that never import optional SDK packages."""

from __future__ import annotations

import json

from pangi.application.contracts.model_routing import GuardedModelRequest, ModelInputSource
from pangi.domain.model_routing import ModelMessageRole


class OptionalModelProviderDependencyError(RuntimeError):
    """Safe installation guidance for an explicitly selected Provider extra."""

    def __init__(self, extra: str) -> None:
        guidance = (
            "pangi-agent[openai] or pangi-agent[bedrock]"
            if extra == "model"
            else f"pangi-agent[{extra}]"
        )
        super().__init__(f"Model Provider dependency is missing; install {guidance}")
        self.extra = extra


def render_model_source(source: ModelInputSource) -> str:
    """Render one already-redacted source as a deterministic data block."""

    payload: dict[str, object] = {
        "content": source.content,
        "source_kind": source.source_kind,
    }
    if source.canonical_data_json is not None:
        payload["data"] = json.loads(source.canonical_data_json)
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def split_rendered_sources(request: GuardedModelRequest) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ordered System and User blocks for Provider-specific mapping."""

    system: list[str] = []
    user: list[str] = []
    for source in request.sources:
        rendered = render_model_source(source)
        if source.role is ModelMessageRole.SYSTEM:
            system.append(rendered)
        else:
            user.append(rendered)
    return tuple(system), tuple(user)
