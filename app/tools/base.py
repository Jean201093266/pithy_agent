from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# MCP-style content / result types
# ---------------------------------------------------------------------------

@dataclass
class MCPContentItem:
    """A single content item in an MCP tool result (text, image, resource…)."""
    type: str  # "text" | "image" | "resource"
    text: str = ""
    data: str = ""        # base64 for image
    mime_type: str = ""
    uri: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.text:
            d["text"] = self.text
        if self.data:
            d["data"] = self.data
        if self.mime_type:
            d["mimeType"] = self.mime_type
        if self.uri:
            d["uri"] = self.uri
        return d


@dataclass
class MCPToolResult:
    """Standardised MCP tool result envelope."""
    content: list[MCPContentItem] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def from_text(cls, text: str, is_error: bool = False) -> "MCPToolResult":
        return cls(content=[MCPContentItem(type="text", text=text)], is_error=is_error)

    @classmethod
    def from_dict(cls, data: Any) -> "MCPToolResult":
        """Wrap an arbitrary Python value into an MCPToolResult."""
        import json as _json
        if isinstance(data, str):
            text = data
        else:
            try:
                text = _json.dumps(data, ensure_ascii=False)
            except Exception:
                text = str(data)
        return cls(content=[MCPContentItem(type="text", text=text)])

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": [c.to_dict() for c in self.content],
            "isError": self.is_error,
        }

    def text(self) -> str:
        return "\n".join(c.text for c in self.content if c.type == "text")


# ---------------------------------------------------------------------------
# MCP server configuration
# ---------------------------------------------------------------------------

@dataclass
class MCPServerConfig:
    """Configuration for an external MCP server."""
    server_id: str
    transport: str          # "stdio" | "http"
    # stdio
    command: str = ""       # e.g. "npx -y @modelcontextprotocol/server-filesystem /"
    # http
    base_url: str = ""      # e.g. "http://localhost:8080"
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""


# ---------------------------------------------------------------------------
# Builtin tool definition
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    risk_level: str  # normal|high
    handler: Callable[[dict[str, Any]], Any]
    source: str = "builtin"
    input_schema: dict[str, Any] = field(default_factory=dict)
    """JSON Schema for the tool's input parameters (MCP inputSchema format)."""


@dataclass
class ToolManifest:
    name: str
    description: str
    risk_level: str
    target_tool: str
    default_params: dict[str, Any] = field(default_factory=dict)
    param_mapping: dict[str, str] = field(default_factory=dict)
    version: str = "1.0.0"
