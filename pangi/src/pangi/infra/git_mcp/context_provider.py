from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pangi.config import Settings
from pangi.infra.git_mcp.mcp_client import (
    GitMcpAuthError,
    GitMcpError,
    GitMcpHttpClient,
    GitMcpTool,
    GitMcpToolResult,
)
from pangi.usecase.git_context import (
    GitContext,
    GitContextAccessDeniedError,
    GitContextDisabledError,
    GitContextSource,
    GitRepoCatalog,
    GitRepoCatalogItem,
)


logger = logging.getLogger(__name__)
REPO_NAME_PATTERN = re.compile(r"(?im)^\s*[-*]?\s*(?:[\w.-]+/)?([A-Za-z0-9_.-]+)\s*$")
FULL_NAME_PATTERN = re.compile(r"(?i)\b[A-Za-z0-9_.-]+/([A-Za-z0-9_.-]+)\b")
INVALID_REPO_NAMES = frozenset({"null", "none", "true", "false"})
PR_QUERY_PATTERN = re.compile(r"(?i)\b(?:pr|pull request|pullrequest|풀리퀘)\b")
ISSUE_QUERY_PATTERN = re.compile(r"(?i)\b(?:issue|이슈)\b")
ACTIONS_QUERY_PATTERN = re.compile(r"(?i)\b(?:actions|action|workflow|ci|액션)\b")
COMMIT_QUERY_PATTERN = re.compile(r"(?i)\b(?:commit|커밋|branch|브랜치|release|릴리즈)\b")
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
REPO_CATALOG_TOOL_NAMES = (
    "search_orgs",
    "list_organization_repositories",
    "search_repositories",
)
PR_CONTEXT_TOOL_NAMES = (
    "search_pull_requests",
    "search_prs",
    "list_pull_requests",
    "get_pull_request",
)
ISSUE_CONTEXT_TOOL_NAMES = (
    "search_issues",
    "list_issues",
    "get_issue",
)
ACTIONS_CONTEXT_TOOL_NAMES = (
    "search_workflow_runs",
    "list_workflow_runs",
    "list_workflows",
    "search_actions",
)
REPO_CONTEXT_TOOL_NAMES = (
    "search_repositories",
    "search_code",
    "search_commits",
    "get_repository",
    "list_repositories",
)


class GitMcpClientFactory(Protocol):
    def __call__(self, mcp_url: str) -> GitMcpHttpClient:
        ...


@dataclass(frozen=True)
class GitMcpRoute:
    mcp_url: str
    source_title: str
    source_type: str
    tool_role: "GitMcpToolRole"
    preferred_tool_names: tuple[str, ...]
    query_text: str | None = None
    org: str | None = None
    repo_catalog: bool = False


class GitMcpToolRole(StrEnum):
    PULL_REQUESTS = "pull_requests"
    ISSUES = "issues"
    ACTIONS = "actions"
    REPOS = "repos"


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

        route = _context_route(text=text, settings=self._settings)
        logger.info("Git MCP context route selected: source_type=%s url=%s", route.source_type, route.mcp_url)
        client = self._mcp_client_factory(route.mcp_url)
        try:
            tools = await client.list_tools()
            tool = _select_tool(
                tools,
                role=route.tool_role,
                preferred_names=route.preferred_tool_names,
            )
            arguments = _build_context_arguments(tool=tool, route=route)
            result = await client.call_tool_result(tool.name, arguments)
        except GitMcpAuthError as error:
            raise GitContextAccessDeniedError("Git MCP authentication failed") from error

        content = _result_to_context_text(result)
        if not content:
            raise GitMcpError("Git MCP returned empty context")

        return GitContext(
            markdown=_truncate_context(content, self._settings.git_mcp_context_max_chars),
            sources=(GitContextSource(title=route.source_title, source_type=route.source_type, url=route.mcp_url),),
        )

    async def fetch_repo_catalog(self, *, local_repo_keys: tuple[str, ...]) -> GitRepoCatalog:
        if not self._settings.git_mcp_enabled:
            raise GitContextDisabledError("Git MCP context is disabled")

        if not self._settings.git_mcp_org:
            return _merge_repo_catalog(
                local_repo_keys=local_repo_keys,
                remote_repo_names=(),
                git_mcp_enabled=False,
                org=None,
            )

        route = GitMcpRoute(
            mcp_url=self._settings.git_mcp_repos_url,
            source_title="GitHub Repositories MCP",
            source_type="git-mcp-repos",
            tool_role=GitMcpToolRole.REPOS,
            preferred_tool_names=REPO_CONTEXT_TOOL_NAMES,
            org=self._settings.git_mcp_org,
            repo_catalog=True,
        )
        logger.info("Git MCP repo catalog route selected: url=%s org=%s", route.mcp_url, route.org)
        client = self._mcp_client_factory(route.mcp_url)
        try:
            tools = await client.list_tools()
            tool = _select_tool(
                tools,
                role=route.tool_role,
                preferred_names=route.preferred_tool_names,
            )
            arguments = _build_repo_catalog_arguments(tool=tool, org=route.org or "")
            result = await client.call_tool_result(tool.name, arguments)
        except GitMcpAuthError as error:
            raise GitContextAccessDeniedError("Git MCP authentication failed") from error

        remote_repo_names = _extract_repo_names_from_result(result, org=route.org or "")
        logger.info(
            "Git MCP repo catalog result: tool=%s remote_repo_count=%d local_repo_count=%d",
            tool.name,
            len(remote_repo_names),
            len(local_repo_keys),
        )
        return _merge_repo_catalog(
            local_repo_keys=local_repo_keys,
            remote_repo_names=remote_repo_names,
            git_mcp_enabled=True,
            org=route.org,
        )

    def _default_mcp_client(self, mcp_url: str) -> GitMcpHttpClient:
        return GitMcpHttpClient(
            mcp_url=mcp_url,
            access_token=self._settings.git_mcp_token,
            timeout_seconds=self._settings.git_mcp_timeout_seconds,
        )


def _context_route(*, text: str, settings: Settings) -> GitMcpRoute:
    if PR_QUERY_PATTERN.search(text):
        return GitMcpRoute(
            mcp_url=settings.git_mcp_pull_requests_url,
            source_title="GitHub Pull Requests MCP",
            source_type="git-mcp-pull-requests",
            tool_role=GitMcpToolRole.PULL_REQUESTS,
            preferred_tool_names=PR_CONTEXT_TOOL_NAMES,
            query_text=_scoped_query(text=text, org=settings.git_mcp_org),
            org=settings.git_mcp_org,
        )
    if ISSUE_QUERY_PATTERN.search(text):
        return GitMcpRoute(
            mcp_url=settings.git_mcp_issues_url,
            source_title="GitHub Issues MCP",
            source_type="git-mcp-issues",
            tool_role=GitMcpToolRole.ISSUES,
            preferred_tool_names=ISSUE_CONTEXT_TOOL_NAMES,
            query_text=_scoped_query(text=text, org=settings.git_mcp_org),
            org=settings.git_mcp_org,
        )
    if ACTIONS_QUERY_PATTERN.search(text):
        return GitMcpRoute(
            mcp_url=settings.git_mcp_actions_url,
            source_title="GitHub Actions MCP",
            source_type="git-mcp-actions",
            tool_role=GitMcpToolRole.ACTIONS,
            preferred_tool_names=ACTIONS_CONTEXT_TOOL_NAMES,
            query_text=_scoped_query(text=text, org=settings.git_mcp_org),
            org=settings.git_mcp_org,
        )
    if COMMIT_QUERY_PATTERN.search(text):
        return GitMcpRoute(
            mcp_url=settings.git_mcp_repos_url,
            source_title="GitHub Repositories MCP",
            source_type="git-mcp-repos",
            tool_role=GitMcpToolRole.REPOS,
            preferred_tool_names=REPO_CONTEXT_TOOL_NAMES,
            query_text=_scoped_query(text=text, org=settings.git_mcp_org),
            org=settings.git_mcp_org,
        )
    return GitMcpRoute(
        mcp_url=settings.git_mcp_repos_url,
        source_title="GitHub Repositories MCP",
        source_type="git-mcp-repos",
        tool_role=GitMcpToolRole.REPOS,
        preferred_tool_names=REPO_CONTEXT_TOOL_NAMES,
        query_text=_scoped_query(text=text, org=settings.git_mcp_org),
        org=settings.git_mcp_org,
    )


def _scoped_query(*, text: str, org: str | None) -> str:
    if org:
        return f"org:{org} {text}"
    return text


def _select_tool(
    tools: tuple[GitMcpTool, ...],
    *,
    role: GitMcpToolRole,
    preferred_names: tuple[str, ...],
) -> GitMcpTool:
    by_name = {tool.name: tool for tool in tools}
    for name in preferred_names:
        if name in by_name:
            return by_name[name]
    if len(tools) == 1 and role != GitMcpToolRole.REPOS:
        logger.info("Git MCP tool fallback: single tool in %s toolset -> %s", role.value, tools[0].name)
        return tools[0]
    available = ", ".join(tool.name for tool in tools) or "(none)"
    expected = ", ".join(preferred_names)
    raise GitMcpError(f"Git MCP missing expected tool. expected one of: {expected}; available: {available}")


def _build_repo_catalog_arguments(*, tool: GitMcpTool, org: str) -> dict[str, Any]:
    schema = tool.input_schema or {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if "org" in properties:
        return {"org": org}
    if "organization" in properties:
        return {"organization": org}
    if "owner" in properties:
        return {"owner": org}
    if "query" in properties:
        return {"query": f"org:{org}"}
    if "q" in properties:
        return {"q": f"org:{org}"}
    return {"org": org}


def _build_context_arguments(*, tool: GitMcpTool, route: GitMcpRoute) -> dict[str, Any]:
    schema = tool.input_schema or {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    query_text = route.query_text or ""
    if "query" in properties:
        return {"query": query_text}
    if "q" in properties:
        return {"q": query_text}
    if "text" in properties:
        return {"text": query_text}
    if "search" in properties:
        return {"search": query_text}
    if route.org and "org" in properties:
        return {"org": route.org}
    if route.org and "organization" in properties:
        return {"organization": route.org}
    if route.org and "owner" in properties:
        return {"owner": route.org}
    return {"query": query_text}


def _result_to_context_text(result: GitMcpToolResult) -> str:
    if result.text.strip():
        return result.text.strip()
    if result.structured_content:
        return json.dumps(result.structured_content, ensure_ascii=False, indent=2)
    return ""


def _extract_repo_names_from_result(result: GitMcpToolResult, *, org: str) -> tuple[str, ...]:
    if result.structured_content is not None:
        names = _collect_repo_names_from_value(result.structured_content, org=org)
        if names:
            return names
    return _extract_repo_names(result.text, org=org)


def _collect_repo_names_from_value(value: object, *, org: str) -> tuple[str, ...]:
    names: list[str] = []
    _collect_repo_names(value, names=names, org=org)
    return tuple(sorted(dict.fromkeys(name for name in names if _is_repo_name(name, org=org))))


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
