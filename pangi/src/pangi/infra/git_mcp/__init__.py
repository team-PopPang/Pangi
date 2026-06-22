"""Git MCP infrastructure adapter registry."""

from __future__ import annotations

from pangi.config import get_settings
from pangi.infra.git_mcp.context_provider import GitMcpContextProvider
from pangi.usecase.ports import GitContextProvider


_git_context_provider: GitContextProvider | None = None


def get_git_context_provider() -> GitContextProvider | None:
    global _git_context_provider
    if _git_context_provider is not None:
        return _git_context_provider
    settings = get_settings()
    if not settings.git_mcp_enabled:
        return None
    _git_context_provider = GitMcpContextProvider(settings=settings)
    return _git_context_provider


def set_git_context_provider(provider: GitContextProvider | None) -> None:
    global _git_context_provider
    _git_context_provider = provider


__all__ = [
    "GitMcpContextProvider",
    "get_git_context_provider",
    "set_git_context_provider",
]
