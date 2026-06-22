from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MCP_USER_AGENT = "Pangi-Notion-MCP-Client/0.1"
MCP_PROTOCOL_VERSION = "2025-06-18"


class NotionMcpError(RuntimeError):
    """Raised when the Notion MCP server cannot return usable data."""


class NotionMcpAuthError(NotionMcpError):
    """Raised when Notion MCP authentication fails."""


@dataclass(frozen=True)
class NotionMcpTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None


class NotionMcpHttpClient:
    def __init__(self, *, mcp_url: str, access_token: str, timeout_seconds: int) -> None:
        self._mcp_url = mcp_url.rstrip("/")
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds
        self._session_id: str | None = None
        self._initialized = False
        self._request_id = 0

    async def list_tools(self) -> tuple[NotionMcpTool, ...]:
        result = await self._request("tools/list", {})
        raw_tools = result.get("tools") or []
        tools: list[NotionMcpTool] = []
        for item in raw_tools:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            tools.append(
                NotionMcpTool(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    input_schema=item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else None,
                )
            )
        return tuple(tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        if result.get("isError") is True:
            raise NotionMcpError(f"Notion MCP tool failed: {name}")
        return _tool_result_to_text(result)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            await self._initialize()
        payload = self._json_rpc_payload(method, params)
        response, _headers = await asyncio.to_thread(
            self._post_json_rpc,
            payload,
            self._session_id,
        )
        return _json_rpc_result(response)

    async def _initialize(self) -> None:
        payload = self._json_rpc_payload(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pangi", "version": "0.1.0"},
            },
        )
        response, headers = await asyncio.to_thread(self._post_json_rpc, payload, None)
        _json_rpc_result(response)
        self._session_id = _find_header(headers, "mcp-session-id")
        self._initialized = True
        await self._send_initialized_notification()

    async def _send_initialized_notification(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            await asyncio.to_thread(self._post_json_rpc, payload, self._session_id)
        except NotionMcpError:
            return

    def _json_rpc_payload(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

    def _post_json_rpc(
        self,
        payload: dict[str, Any],
        session_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": MCP_USER_AGENT,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = Request(self._mcp_url, data=body, method="POST", headers=headers)
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as error:
            if error.code in {401, 403}:
                raise NotionMcpAuthError("Notion MCP authentication failed") from error
            raise NotionMcpError("Notion MCP HTTP request failed") from error
        except (URLError, TimeoutError) as error:
            raise NotionMcpError("Notion MCP HTTP request failed") from error
        return _parse_json_or_sse(raw), response_headers


def _json_rpc_result(response: dict[str, Any]) -> dict[str, Any]:
    if "error" in response:
        raise NotionMcpError("Notion MCP returned JSON-RPC error")
    result = response.get("result")
    if not isinstance(result, dict):
        raise NotionMcpError("Notion MCP returned invalid JSON-RPC result")
    return result


def _parse_json_or_sse(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise NotionMcpError("Notion MCP returned invalid JSON")
        return data

    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if not data_lines:
        raise NotionMcpError("Notion MCP returned unsupported response")
    data = json.loads("\n".join(data_lines))
    if not isinstance(data, dict):
        raise NotionMcpError("Notion MCP returned invalid SSE JSON")
    return data


def _tool_result_to_text(result: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict):
            if item.get("type") == "text" and item.get("text"):
                chunks.append(str(item["text"]))
            elif item.get("text"):
                chunks.append(str(item["text"]))
    structured = result.get("structuredContent")
    if structured:
        chunks.append(json.dumps(structured, ensure_ascii=False, indent=2))
    return "\n\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())


def _find_header(headers: dict[str, str], name: str) -> str | None:
    return headers.get(name.lower())
