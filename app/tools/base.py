from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    risk_level: str  # normal|high
    handler: Callable[[dict[str, Any]], Any]
    source: str = "builtin"


@dataclass
class ToolManifest:
    name: str
    description: str
    risk_level: str
    target_tool: str
    default_params: dict[str, Any] = field(default_factory=dict)
    param_mapping: dict[str, str] = field(default_factory=dict)
    version: str = "1.0.0"

