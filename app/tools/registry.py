from __future__ import annotations

import logging
from typing import Any

from app.core.db import AppDB
from app.tools.base import MCPServerConfig, MCPToolResult, ToolManifest, ToolSpec
from app.tools.builtin import (
    tool_echo,
    tool_capture_screenshot,
    tool_json_parse,
    tool_ocr_image,
    tool_read_file,
    tool_run_command,
    tool_sqlite_query,
    tool_web_search,
    tool_write_file,
)
from app.tools.mcp_client import MCPClientBase, create_mcp_client

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON Schema helpers
# ---------------------------------------------------------------------------

def _str_prop(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _schema(*required: str, **props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(props),
        "required": list(required),
    }


# ---------------------------------------------------------------------------
# Builtin tool definitions with inputSchema (MCP style)
# ---------------------------------------------------------------------------

_BUILTIN_SPECS: list[tuple[str, str, str, Any, dict[str, Any]]] = [
    (
        "read_file",
        "Read local file content (up to 5000 chars)",
        "normal",
        tool_read_file,
        _schema("path", path=_str_prop("Absolute or ~ path of the file to read")),
    ),
    (
        "write_file",
        "Write text content to a local file",
        "normal",
        tool_write_file,
        _schema("path", "content",
                path=_str_prop("Destination file path"),
                content=_str_prop("Text content to write")),
    ),
    (
        "json_parse",
        "Parse a JSON string and return the parsed object",
        "normal",
        tool_json_parse,
        _schema("text", text=_str_prop("JSON string to parse")),
    ),
    (
        "web_search",
        "Search the web by keyword using DuckDuckGo instant answers",
        "normal",
        tool_web_search,
        _schema("query", query=_str_prop("Search query")),
    ),
    (
        "run_command",
        "Execute a local shell command (high risk – requires authorization)",
        "high",
        tool_run_command,
        _schema("command", command=_str_prop("Shell command to execute")),
    ),
    (
        "echo",
        "Echo the provided message back",
        "normal",
        tool_echo,
        _schema("message", message=_str_prop("Text to echo")),
    ),
    (
        "ocr_image",
        "Extract text from an image using Tesseract OCR",
        "normal",
        tool_ocr_image,
        _schema("path",
                path=_str_prop("Path to the image file"),
                lang=_str_prop("Tesseract language code (default: eng)")),
    ),
    (
        "sqlite_query",
        "Run a read-only SELECT/PRAGMA query against a SQLite database",
        "normal",
        tool_sqlite_query,
        _schema("query",
                db_path=_str_prop("Path to the .db file (default: data/agent.db)"),
                query=_str_prop("SQL SELECT or PRAGMA statement"),
                limit={"type": "integer", "description": "Max rows (1-500, default 100)"}),
    ),
    (
        "capture_screenshot",
        "Capture a desktop screenshot and save it to a file (high risk)",
        "high",
        tool_capture_screenshot,
        _schema(output=_str_prop("Destination image path (default: data/screenshot.png)")),
    ),
]


class ToolRegistry:
    def __init__(self, db: AppDB) -> None:
        self.db = db
        self._builtin_tools: dict[str, ToolSpec] = {
            name: ToolSpec(
                name=name,
                description=desc,
                risk_level=risk,
                handler=handler,
                source="builtin",
                input_schema=schema,
            )
            for name, desc, risk, handler, schema in _BUILTIN_SPECS
        }
        self._custom_manifests: dict[str, ToolManifest] = {}
        # MCP: server_id → client
        self._mcp_clients: dict[str, MCPClientBase] = {}
        # MCP: tool_name → (server_id, mcp_tool_def)
        self._mcp_tools: dict[str, tuple[str, dict[str, Any]]] = {}

        self.reload_custom_tools()
        self._load_mcp_servers_from_db()

    # ------------------------------------------------------------------
    # Custom (manifest-based) tools
    # ------------------------------------------------------------------

    def reload_custom_tools(self) -> None:
        manifests: dict[str, ToolManifest] = {}
        for item in self.db.list_custom_tools():
            manifest = ToolManifest(**item["manifest"])
            manifests[manifest.name] = manifest
        self._custom_manifests = manifests

    def import_manifest(self, payload: dict[str, Any]) -> ToolManifest:
        manifest = ToolManifest(**payload)
        if manifest.name in self._builtin_tools:
            raise ValueError(f"tool name conflicts with builtin tool: {manifest.name}")
        if manifest.target_tool not in self._builtin_tools:
            raise ValueError(f"target_tool not supported: {manifest.target_tool}")
        self.db.upsert_custom_tool(manifest.name, payload)
        self.reload_custom_tools()
        return manifest

    # ------------------------------------------------------------------
    # MCP server management
    # ------------------------------------------------------------------

    def _load_mcp_servers_from_db(self) -> None:
        for row in self.db.list_mcp_servers():
            if not row["enabled"]:
                continue
            try:
                cfg = MCPServerConfig(**row["config"])
                self._connect_mcp_server(cfg)
            except Exception as exc:
                LOGGER.warning("Failed to load MCP server %s: %s", row["server_id"], exc)

    def _connect_mcp_server(self, cfg: MCPServerConfig) -> None:
        """Create an MCP client, call list_tools, register tools."""
        if cfg.server_id in self._mcp_clients:
            self._disconnect_mcp_server(cfg.server_id)
        client = create_mcp_client(cfg)
        try:
            tools = client.list_tools()
        except Exception as exc:
            LOGGER.error("MCP server %s list_tools failed: %s", cfg.server_id, exc)
            client.close()
            raise
        self._mcp_clients[cfg.server_id] = client
        for t in tools:
            tname = t["name"]
            self._mcp_tools[tname] = (cfg.server_id, t)
            LOGGER.info("MCP tool registered: %s (server=%s)", tname, cfg.server_id)

    def _disconnect_mcp_server(self, server_id: str) -> None:
        client = self._mcp_clients.pop(server_id, None)
        if client:
            client.close()
        # Remove tools belonging to this server
        self._mcp_tools = {
            k: v for k, v in self._mcp_tools.items() if v[0] != server_id
        }

    def register_mcp_server(self, cfg: MCPServerConfig) -> list[dict[str, Any]]:
        """Persist and connect an MCP server. Returns its tool list."""
        self.db.upsert_mcp_server(
            cfg.server_id,
            {
                "server_id": cfg.server_id,
                "transport": cfg.transport,
                "command": cfg.command,
                "base_url": cfg.base_url,
                "headers": cfg.headers,
                "enabled": cfg.enabled,
                "description": cfg.description,
            },
            enabled=cfg.enabled,
        )
        if cfg.enabled:
            self._connect_mcp_server(cfg)
        return [t for _, (sid, t) in self._mcp_tools.items() if sid == cfg.server_id]

    def unregister_mcp_server(self, server_id: str) -> bool:
        self._disconnect_mcp_server(server_id)
        return self.db.delete_mcp_server(server_id)

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        rows = self.db.list_mcp_servers()
        result = []
        for row in rows:
            server_id = row["server_id"]
            connected = server_id in self._mcp_clients
            tool_names = [k for k, (sid, _) in self._mcp_tools.items() if sid == server_id]
            result.append({
                **row,
                "connected": connected,
                "tool_count": len(tool_names),
                "tools": tool_names,
            })
        return result

    def refresh_mcp_server(self, server_id: str) -> list[dict[str, Any]]:
        """Re-connect and re-discover tools for a specific MCP server."""
        row = self.db.get_mcp_server(server_id)
        if row is None:
            raise KeyError(server_id)
        cfg = MCPServerConfig(**row["config"])
        self._connect_mcp_server(cfg)
        return [t for _, (sid, t) in self._mcp_tools.items() if sid == server_id]

    # ------------------------------------------------------------------
    # Tool listing (merged: builtin + custom + MCP)
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        out = []
        for name, spec in self._builtin_tools.items():
            out.append({
                "name": name,
                "description": spec.description,
                "risk_level": spec.risk_level,
                "enabled": self.db.is_tool_enabled(name, default=True),
                "source": spec.source,
                "inputSchema": spec.input_schema,
            })
        for name, manifest in self._custom_manifests.items():
            # Inherit inputSchema from target builtin
            target_schema = self._builtin_tools.get(manifest.target_tool)
            out.append({
                "name": name,
                "description": manifest.description,
                "risk_level": manifest.risk_level,
                "enabled": self.db.is_tool_enabled(name, default=True),
                "source": "custom",
                "inputSchema": target_schema.input_schema if target_schema else {},
            })
        for tname, (server_id, tdef) in self._mcp_tools.items():
            out.append({
                "name": tname,
                "description": tdef.get("description", ""),
                "risk_level": "normal",
                "enabled": self.db.is_tool_enabled(tname, default=True),
                "source": f"mcp:{server_id}",
                "inputSchema": tdef.get("inputSchema", {"type": "object", "properties": {}}),
            })
        return out

    def list_custom_manifests(self) -> list[dict[str, Any]]:
        return [
            {
                "name": manifest.name,
                "description": manifest.description,
                "risk_level": manifest.risk_level,
                "target_tool": manifest.target_tool,
                "default_params": manifest.default_params,
                "param_mapping": manifest.param_mapping,
                "version": manifest.version,
                "source": "custom",
            }
            for manifest in self._custom_manifests.values()
        ]

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._builtin_tools and name not in self._custom_manifests and name not in self._mcp_tools:
            raise KeyError(name)
        self.db.set_tool_enabled(name, enabled)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, name: str, params: dict[str, Any], authorized: bool = False) -> Any:
        # MCP tool
        if name in self._mcp_tools:
            if not self.db.is_tool_enabled(name, default=True):
                raise PermissionError(f"tool disabled: {name}")
            server_id, _ = self._mcp_tools[name]
            client = self._mcp_clients.get(server_id)
            if client is None:
                raise RuntimeError(f"MCP server not connected: {server_id}")
            result: MCPToolResult = client.call_tool(name, params)
            return result.to_dict()

        # Builtin or manifest tool
        spec = self._builtin_tools.get(name)
        manifest = self._custom_manifests.get(name)
        if spec is None and manifest is None:
            raise KeyError(name)
        if not self.db.is_tool_enabled(name, default=True):
            raise PermissionError(f"tool disabled: {name}")
        if manifest is not None:
            spec = self._builtin_tools[manifest.target_tool]
            merged_params = dict(manifest.default_params)
            for key, value in params.items():
                mapped_key = manifest.param_mapping.get(key, key)
                merged_params[mapped_key] = value
            params = merged_params
        if spec.risk_level == "high" and not authorized:
            raise PermissionError(f"high-risk tool requires authorization: {name}")
        raw = spec.handler(params)
        # Wrap in MCPToolResult for uniform output
        return MCPToolResult.from_dict(raw).to_dict()
