"""
Lightweight MCP (Model Context Protocol) client.

Supports two transports:
  - stdio  : spawn a subprocess and communicate via JSON-RPC 2.0 on stdin/stdout
  - http   : send JSON-RPC 2.0 requests to an HTTP endpoint

MCP JSON-RPC method subset implemented:
  initialize         → handshake
  tools/list         → discover tools
  tools/call         → invoke a tool
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Any

import requests

from app.tools.base import MCPContentItem, MCPServerConfig, MCPToolResult

LOGGER = logging.getLogger(__name__)

_JSONRPC = "2.0"
_MCP_PROTOCOL_VERSION = "2024-11-05"


def _rpc(method: str, params: dict[str, Any], req_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": _JSONRPC, "id": req_id, "method": method, "params": params}


def _parse_mcp_content(raw: Any) -> MCPToolResult:
    """Convert an MCP tools/call result payload into MCPToolResult."""
    if not isinstance(raw, dict):
        return MCPToolResult.from_text(str(raw))
    is_error = bool(raw.get("isError", False))
    items: list[MCPContentItem] = []
    for c in raw.get("content", []):
        ct = c.get("type", "text")
        items.append(
            MCPContentItem(
                type=ct,
                text=c.get("text", ""),
                data=c.get("data", ""),
                mime_type=c.get("mimeType", ""),
                uri=c.get("uri", ""),
            )
        )
    if not items:
        items = [MCPContentItem(type="text", text=str(raw))]
    return MCPToolResult(content=items, is_error=is_error)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MCPClientBase(ABC):
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    @abstractmethod
    def list_tools(self) -> list[dict[str, Any]]:
        """Return a list of MCP tool definitions (name, description, inputSchema)."""

    @abstractmethod
    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Call a tool by name with the given arguments."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

class HttpMCPClient(MCPClientBase):
    """
    Connects to an MCP server exposed over HTTP.
    Sends JSON-RPC 2.0 POST requests to ``config.base_url``.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        super().__init__(config)
        self._session = requests.Session()
        if config.headers:
            self._session.headers.update(config.headers)
        self._initialized = False
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, method: str, params: dict[str, Any]) -> Any:
        url = self.config.base_url.rstrip("/")
        payload = _rpc(method, params, self._next_id())
        try:
            resp = self._session.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"MCP HTTP error [{self.config.server_id}]: {exc}") from exc
        if "error" in data:
            raise RuntimeError(f"MCP error [{self.config.server_id}]: {data['error']}")
        return data.get("result")

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._post(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "pithy-agent", "version": "0.1.0"},
            },
        )
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_init()
        result = self._post("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self._ensure_init()
        result = self._post("tools/call", {"name": name, "arguments": arguments})
        return _parse_mcp_content(result)

    def close(self) -> None:
        self._session.close()
        self._initialized = False


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------

class StdioMCPClient(MCPClientBase):
    """
    Connects to an MCP server by spawning a subprocess and communicating
    via JSON-RPC 2.0 over stdin/stdout (newline-delimited JSON).
    """

    def __init__(self, config: MCPServerConfig) -> None:
        super().__init__(config)
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._initialized = False

    def _start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        import shlex
        args = shlex.split(self.config.command)
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        LOGGER.info("MCP stdio server started: %s (pid=%s)", self.config.server_id, self._proc.pid)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send_recv(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            self._start()
            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None:
                raise RuntimeError(f"MCP stdio process not running [{self.config.server_id}]")
            payload = _rpc(method, params, self._next_id())
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
                response_line = proc.stdout.readline()
                if not response_line:
                    raise RuntimeError("MCP stdio server closed stdout")
                data = json.loads(response_line)
            except Exception as exc:
                raise RuntimeError(f"MCP stdio error [{self.config.server_id}]: {exc}") from exc
        if "error" in data:
            raise RuntimeError(f"MCP error [{self.config.server_id}]: {data['error']}")
        return data.get("result")

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._send_recv(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "pithy-agent", "version": "0.1.0"},
            },
        )
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_init()
        result = self._send_recv("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self._ensure_init()
        result = self._send_recv("tools/call", {"name": name, "arguments": arguments})
        return _parse_mcp_content(result)

    def close(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None
        self._initialized = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_mcp_client(config: MCPServerConfig) -> MCPClientBase:
    if config.transport == "stdio":
        return StdioMCPClient(config)
    if config.transport == "http":
        return HttpMCPClient(config)
    raise ValueError(f"unsupported MCP transport: {config.transport}")

