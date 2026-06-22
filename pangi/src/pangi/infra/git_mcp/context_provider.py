from __future__ import annotations

import json
import re
from typing import Protocol

from pangi.config import Settings
from pangi.infra.git_mcp.mcp_client import GitMcpAuthError, GitMcpError, GitMcpHttpClient, GitMcpTool
from pangi.usecase.git_context import (
    GitContext,
    GitContextAccessDeniedError,
    GitContextDisabledError,
    GitContextSource,
    GitRepoCatalog,
    GitRepoCatalogItem,
)


REPO_NAME_PATTERN = re.compile(r"(?im)^\s*[-*]?\s*(?:[\w.-]+/)?([A-Za-z0-9_.-]+)\s*$")
FULL_NAME_PATTERN = re.compile(r"(?i)\b[A-Za-z0-9_.-]+/([A-Za-z0-9_.-]+)\b")
INVALID_REPO_NAMES = frozenset({"null", "none", "true", "false"})
WRITE_TOOL_KEYWORDS = (
    "create",
    "update",
    "delete",
    "remove",
    "merge",
    "push",
    "close",
    "reopen",
    "assign",
    "label",
    "comment",
    "approve",
    "review",
    "request_changes",
    "rerun",
    "cancel",
    "dispatch",
)


class GitMcpClientFactory(Protocol):
    def __call__(self) -> GitMcpHttpClient:
        ...


class GitMcpContextProvider:
    def __init__(
        self,
        *,
        settings: Settings,
        mcp_client_factory: GitMcpClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._mcp_client_factory = mcp_client_factory or self._default_mcp_client

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> GitContext:
        if not self._settings.git_mcp_enabled:
            raise GitContextDisabledError("Git MCP context is disabled")

        client = self._mcp_client_factory()
        try:
            tools = await client.list_tools()
            content = await _fetch_git_context(
                client=client,
                tools=tools,
                text=text,
                org=self._settings.git_mcp_org,
                allow_write_tools=self._settings.git_mcp_write_enabled,
            )
        except GitMcpAuthError as error:
            raise GitContextAccessDeniedError("Git MCP authentication failed") from error
        if not content:
            raise GitMcpError("Git MCP returned empty context")

        return GitContext(
            markdown=_truncate_context(content, self._settings.git_mcp_context_max_chars),
            sources=(GitContextSource(title="Git MCP", source_type="git-mcp"),),
        )

    async def fetch_repo_catalog(self, *, local_repo_keys: tuple[str, ...]) -> GitRepoCatalog:
        if not self._settings.git_mcp_enabled:
            raise GitContextDisabledError("Git MCP context is disabled")

        remote_repo_names: tuple[str, ...] = ()
        if self._settings.git_mcp_org:
            client = self._mcp_client_factory()
            try:
                tools = await client.list_tools()
                raw_catalog = await _fetch_repo_catalog_text(
                    client=client,
                    tools=tools,
                    org=self._settings.git_mcp_org,
                    allow_write_tools=self._settings.git_mcp_write_enabled,
                )
            except GitMcpAuthError as error:
                raise GitContextAccessDeniedError("Git MCP authentication failed") from error
            remote_repo_names = _extract_repo_names(raw_catalog, org=self._settings.git_mcp_org)

        return _merge_repo_catalog(
            local_repo_keys=local_repo_keys,
            remote_repo_names=remote_repo_names,
            git_mcp_enabled=True,
            org=self._settings.git_mcp_org,
        )

    def _default_mcp_client(self) -> GitMcpHttpClient:
        return GitMcpHttpClient(
            mcp_url=self._settings.git_mcp_url,
            access_token=self._settings.git_mcp_token,
            timeout_seconds=self._settings.git_mcp_timeout_seconds,
        )


async def _fetch_git_context(
    *,
    client: GitMcpHttpClient,
    tools: tuple[GitMcpTool, ...],
    text: str,
    org: str | None,
    allow_write_tools: bool,
) -> str:
    argument_sets = _context_argument_sets(text=text, org=org)
    last_error: Exception | None = None
    for tool in _context_candidate_tools(tools, text=text, allow_write_tools=allow_write_tools):
        for arguments in argument_sets:
            try:
                content = await client.call_tool(tool.name, arguments)
            except GitMcpAuthError:
                raise
            except GitMcpError as error:
                last_error = error
                continue
            if content.strip():
                return content.strip()
    if last_error is not None:
        return ""
    return ""


async def _fetch_repo_catalog_text(
    *,
    client: GitMcpHttpClient,
    tools: tuple[GitMcpTool, ...],
    org: str,
    allow_write_tools: bool,
) -> str:
    argument_sets = (
        {"org": org},
        {"organization": org},
        {"owner": org},
        {"query": f"org:{org}"},
        {"q": f"org:{org}"},
    )
    for tool in _repo_catalog_candidate_tools(tools, allow_write_tools=allow_write_tools):
        for arguments in argument_sets:
            try:
                content = await client.call_tool(tool.name, arguments)
            except GitMcpAuthError:
                raise
            except GitMcpError:
                continue
            if content.strip():
                return content.strip()
    return ""


def _context_candidate_tools(
    tools: tuple[GitMcpTool, ...],
    *,
    text: str,
    allow_write_tools: bool,
) -> tuple[GitMcpTool, ...]:
    lowered = text.lower()
    include_any = ["search", "get", "list", "repository", "repo", "pull", "issue", "action", "workflow", "commit"]
    if "pr" in lowered or "pull" in lowered or "풀리퀘" in lowered:
        include_any = ["pull", "pr", "search"]
    elif "issue" in lowered or "이슈" in lowered:
        include_any = ["issue", "search"]
    elif "action" in lowered or "workflow" in lowered or "ci" in lowered or "액션" in lowered:
        include_any = ["action", "workflow", "run", "search"]
    elif "commit" in lowered or "커밋" in lowered:
        include_any = ["commit", "search"]
    return _candidate_tools(tools, include_any=tuple(include_any), allow_write_tools=allow_write_tools)


def _repo_catalog_candidate_tools(
    tools: tuple[GitMcpTool, ...],
    *,
    allow_write_tools: bool,
) -> tuple[GitMcpTool, ...]:
    return _candidate_tools(
        tools,
        include_any=("repo", "repository", "org", "organization", "search", "list"),
        allow_write_tools=allow_write_tools,
    )


def _candidate_tools(
    tools: tuple[GitMcpTool, ...],
    *,
    include_any: tuple[str, ...],
    allow_write_tools: bool,
) -> tuple[GitMcpTool, ...]:
    ranked: list[tuple[int, GitMcpTool]] = []
    for tool in tools:
        haystack = f"{tool.name} {tool.description}".lower()
        if not allow_write_tools and _looks_like_write_tool(haystack):
            continue
        score = sum(1 for keyword in include_any if keyword in haystack)
        if score:
            ranked.append((score, tool))
    return tuple(tool for _score, tool in sorted(ranked, key=lambda item: (-item[0], item[1].name)))


def _looks_like_write_tool(haystack: str) -> bool:
    normalized = haystack.replace("-", "_").replace(" ", "_")
    return any(keyword in normalized for keyword in WRITE_TOOL_KEYWORDS)


def _context_argument_sets(*, text: str, org: str | None) -> tuple[dict[str, object], ...]:
    arguments: list[dict[str, object]] = [
        {"query": text},
        {"q": text},
        {"text": text},
    ]
    if org:
        arguments.extend(
            [
                {"query": text, "owner": org},
                {"query": text, "org": org},
                {"q": f"org:{org} {text}"},
            ]
        )
    return tuple(arguments)


def _extract_repo_names(raw_text: str, *, org: str) -> tuple[str, ...]:
    if not raw_text.strip():
        return ()

    names = _extract_repo_names_from_json(raw_text, org=org)
    if names is not None and (names or _is_json_primitive_catalog(raw_text)):
        return names

    found: list[str] = []
    for match in FULL_NAME_PATTERN.finditer(raw_text):
        found.append(match.group(1))
    for match in REPO_NAME_PATTERN.finditer(raw_text):
        found.append(match.group(1))
    return tuple(sorted(dict.fromkeys(name for name in found if _is_repo_name(name, org=org))))


def _extract_repo_names_from_json(raw_text: str, *, org: str) -> tuple[str, ...] | None:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    names: list[str] = []
    _collect_repo_names(data, names=names, org=org)
    return tuple(sorted(dict.fromkeys(name for name in names if _is_repo_name(name, org=org))))


def _collect_repo_names(value: object, *, names: list[str], org: str) -> None:
    if isinstance(value, dict):
        raw_name = value.get("full_name") or value.get("name")
        if isinstance(raw_name, str):
            names.append(raw_name.split("/")[-1])
        for child in value.values():
            _collect_repo_names(child, names=names, org=org)
    elif isinstance(value, list):
        for item in value:
            _collect_repo_names(item, names=names, org=org)
    elif isinstance(value, str) and value.startswith(f"{org}/"):
        names.append(value.split("/", 1)[1])


def _is_repo_name(value: str, *, org: str) -> bool:
    name = value.strip()
    return bool(name and name != org and name.lower() not in INVALID_REPO_NAMES and REPO_NAME_PATTERN.fullmatch(name))


def _is_json_primitive_catalog(raw_text: str) -> bool:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return False
    return data is None or isinstance(data, (bool, int, float))


def _merge_repo_catalog(
    *,
    local_repo_keys: tuple[str, ...],
    remote_repo_names: tuple[str, ...],
    git_mcp_enabled: bool,
    org: str | None,
) -> GitRepoCatalog:
    local = set(local_repo_keys)
    remote = set(remote_repo_names)
    names = tuple(sorted(local | remote))
    items: list[GitRepoCatalogItem] = []
    for name in names:
        if name in local and (not remote or name in remote):
            status = "ready"
        elif name in remote:
            status = "clone_on_demand"
        else:
            status = "local_only"
        items.append(GitRepoCatalogItem(name=name, status=status))
    return GitRepoCatalog(items=tuple(items), git_mcp_enabled=git_mcp_enabled, org=org)


def _truncate_context(markdown: str, max_chars: int) -> str:
    if len(markdown) <= max_chars:
        return markdown
    suffix = "\n\n... Git context가 길어 일부를 생략했습니다."
    return markdown[: max(0, max_chars - len(suffix))].rstrip() + suffix
