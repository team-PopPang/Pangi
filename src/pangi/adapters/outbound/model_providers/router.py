"""Select exactly the Provider admitted by the active Model Policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pangi.adapters.outbound.model_providers.bedrock import create_bedrock_provider
from pangi.adapters.outbound.model_providers.common import (
    OptionalModelProviderDependencyError,
)
from pangi.adapters.outbound.model_providers.openai import create_openai_provider
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelProfile,
    ModelProviderFailure,
    ModelProviderResponse,
    ProviderRetryPolicy,
)
from pangi.application.ports.model_routing import ModelProvider
from pangi.domain.model_routing import ModelProviderErrorCode

ProviderFactory = Callable[[ModelProfile, ProviderRetryPolicy], ModelProvider]


def _openai_factory(
    profile: ModelProfile,
    retry_policy: ProviderRetryPolicy,
) -> ModelProvider:
    del profile
    return create_openai_provider(retry_policy=retry_policy)


def _bedrock_factory(
    profile: ModelProfile,
    retry_policy: ProviderRetryPolicy,
) -> ModelProvider:
    if profile.region is None:
        raise ValueError("Bedrock requires a selected region")
    return create_bedrock_provider(
        region_name=profile.region,
        retry_policy=retry_policy,
    )


_DEFAULT_FACTORIES: Mapping[str, ProviderFactory] = {
    "bedrock": _bedrock_factory,
    "openai": _openai_factory,
}


class PolicySelectedModelProvider:
    """Lazily create only the Provider selected by the guarded request."""

    def __init__(
        self,
        retry_policy: ProviderRetryPolicy,
        *,
        factories: Mapping[str, ProviderFactory] | None = None,
    ) -> None:
        if not isinstance(retry_policy, ProviderRetryPolicy):
            raise TypeError("retry_policy must be a ProviderRetryPolicy")
        selected_factories = _DEFAULT_FACTORIES if factories is None else factories
        if not selected_factories or any(
            not isinstance(name, str) or not callable(factory)
            for name, factory in selected_factories.items()
        ):
            raise ValueError("factories must map Provider names to callables")
        self._retry_policy = retry_policy
        self._factories = dict(selected_factories)
        self._providers: dict[tuple[str, str | None], ModelProvider] = {}

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        if not self._selection_matches(request):
            raise ModelProviderFailure(
                ModelProviderErrorCode.INVALID_REQUEST,
                retryable=False,
            )
        provider = self._provider_for(request.profile)
        return await provider.invoke(request)

    @staticmethod
    def _selection_matches(request: GuardedModelRequest) -> bool:
        profile = request.profile
        decision = request.decision
        return (
            decision.selected_profile_fingerprint == profile.fingerprint
            and decision.provider == profile.provider
            and decision.model == profile.model
            and decision.region == profile.region
        )

    def _provider_for(self, profile: ModelProfile) -> ModelProvider:
        key = (profile.provider, profile.region)
        cached = self._providers.get(key)
        if cached is not None:
            return cached

        factory = self._factories.get(profile.provider)
        if factory is None:
            raise ModelProviderFailure(
                ModelProviderErrorCode.INVALID_REQUEST,
                retryable=False,
            )
        try:
            provider = factory(profile, self._retry_policy)
        except OptionalModelProviderDependencyError:
            raise ModelProviderFailure(
                ModelProviderErrorCode.UNAVAILABLE,
                retryable=False,
            ) from None
        except ModelProviderFailure:
            raise
        except ValueError:
            raise ModelProviderFailure(
                ModelProviderErrorCode.INVALID_REQUEST,
                retryable=False,
            ) from None
        except Exception:
            raise ModelProviderFailure(
                ModelProviderErrorCode.UNKNOWN,
                retryable=False,
            ) from None

        if not callable(getattr(provider, "invoke", None)):
            raise ModelProviderFailure(
                ModelProviderErrorCode.UNKNOWN,
                retryable=False,
            )
        self._providers[key] = provider
        return provider
