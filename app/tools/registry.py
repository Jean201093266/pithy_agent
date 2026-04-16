from __future__ import annotations

from typing import Any

from app.core.db import AppDB
from app.tools.base import ToolManifest, ToolSpec
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


class ToolRegistry:
    def __init__(self, db: AppDB) -> None:
        self.db = db
        self._builtin_tools: dict[str, ToolSpec] = {
            "read_file": ToolSpec("read_file", "Read local file content", "normal", tool_read_file),
            "write_file": ToolSpec("write_file", "Write local file content", "normal", tool_write_file),
            "json_parse": ToolSpec("json_parse", "Parse JSON text", "normal", tool_json_parse),
            "web_search": ToolSpec("web_search", "Search web by keyword", "normal", tool_web_search),
            "run_command": ToolSpec("run_command", "Execute local shell command", "high", tool_run_command),
            "echo": ToolSpec("echo", "Echo input parameters", "normal", tool_echo),
            "ocr_image": ToolSpec("ocr_image", "Extract text from image using OCR", "normal", tool_ocr_image),
            "sqlite_query": ToolSpec("sqlite_query", "Run read-only SQLite query", "normal", tool_sqlite_query),
            "capture_screenshot": ToolSpec("capture_screenshot", "Capture desktop screenshot", "high", tool_capture_screenshot),
        }
        self._custom_manifests: dict[str, ToolManifest] = {}
        self.reload_custom_tools()

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

    def list_tools(self) -> list[dict[str, Any]]:
        out = []
        for name, spec in self._builtin_tools.items():
            out.append(
                {
                    "name": name,
                    "description": spec.description,
                    "risk_level": spec.risk_level,
                    "enabled": self.db.is_tool_enabled(name, default=True),
                    "source": spec.source,
                }
            )
        for name, manifest in self._custom_manifests.items():
            out.append(
                {
                    "name": name,
                    "description": manifest.description,
                    "risk_level": manifest.risk_level,
                    "enabled": self.db.is_tool_enabled(name, default=True),
                    "source": "custom",
                }
            )
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
        if name not in self._builtin_tools and name not in self._custom_manifests:
            raise KeyError(name)
        self.db.set_tool_enabled(name, enabled)

    def execute(self, name: str, params: dict[str, Any], authorized: bool = False) -> Any:
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
        return spec.handler(params)

