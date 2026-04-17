from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelConfigIn(BaseModel):
    provider: Literal["mock", "openai", "openai-compatible", "tongyi", "wenxin"] = Field(default="mock")
    model: str = Field(default="mock-model")
    api_key: str = Field(default="")
    secret_key: str = Field(default="")
    base_url: str = Field(default="")
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class ModelConfigOut(BaseModel):
    provider: Literal["mock", "openai", "openai-compatible", "tongyi", "wenxin"]
    model: str
    base_url: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    has_api_key: bool
    has_secret_key: bool


class AppSettingsIn(BaseModel):
    theme: Literal["system", "light", "dark"] = "system"
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    log_lines: int = Field(default=120, ge=20, le=500)
    log_level: str = Field(default="INFO")
    auto_refresh_logs: bool = False
    send_shortcut: str = Field(default="Ctrl+Enter")


class AppSettingsOut(AppSettingsIn):
    pass


class SecurityStatusResponse(BaseModel):
    has_password: bool
    locked: bool
    failed_attempts: int
    theme: Literal["system", "light", "dark"]
    language: Literal["zh-CN", "en-US"]


class PasswordSetupRequest(BaseModel):
    password: str = Field(min_length=4, max_length=128)


class UnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class AuthSessionResponse(BaseModel):
    ok: bool
    token: str | None = None
    has_password: bool
    locked: bool
    failed_attempts: int = 0


class LogsResponse(BaseModel):
    lines: list[str]


class SigningInfo(BaseModel):
    status: str = "unknown"
    isSigned: bool = False
    subject: str | None = None
    issuer: str | None = None
    thumbprint: str | None = None
    notAfter: str | None = None


class ReleaseInfoResponse(BaseModel):
    name: str
    product_name: str
    version: str
    build_time: str | None = None
    commit: str | None = None
    channel: str = "latest"
    signing: SigningInfo


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    force_tool: str | None = None
    tool_params: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(default="default", min_length=1, max_length=120)


class ChatResponse(BaseModel):
    language: str
    plan: list[str]
    used_tool: str | None
    tool_result: Any | None
    reply: str
    brain: dict[str, Any] | None = None


class ToolExecutionRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    authorized: bool = False


class ToolStatePatch(BaseModel):
    enabled: bool


class ToolManifestIn(BaseModel):
    name: str = Field(min_length=2)
    description: str = Field(default="")
    risk_level: Literal["normal", "high"] = "normal"
    target_tool: str = Field(min_length=1)
    default_params: dict[str, Any] = Field(default_factory=dict)
    param_mapping: dict[str, str] = Field(default_factory=dict)
    version: str = "1.0.0"


class ToolManifestOut(ToolManifestIn):
    source: Literal["builtin", "custom"]


class ToolImportResponse(BaseModel):
    ok: bool
    tool: ToolManifestOut


class SkillStep(BaseModel):
    kind: Literal["tool", "llm"]
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class SkillSpec(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    steps: list[SkillStep] = Field(default_factory=list)


class SkillRunRequest(BaseModel):
    input_text: str
    context: dict[str, Any] = Field(default_factory=dict)


class SkillImportRequest(BaseModel):
    format: Literal["json", "yaml", "auto"] = "auto"
    content: str = Field(min_length=2)
    overwrite_latest: bool = True


class SkillImportResponse(BaseModel):
    ok: bool
    skill_id: int
    name: str
    version: str
    imported_format: Literal["json", "yaml"]


class SkillExportResponse(BaseModel):
    ok: bool
    skill_id: int
    name: str
    version: str
    format: Literal["json", "yaml"]
    content: str


class SkillVersionItem(BaseModel):
    version_id: int
    version: str
    source: str
    created_at: str


class SkillVersionsResponse(BaseModel):
    skill_id: int
    name: str
    versions: list[SkillVersionItem]


class SkillRollbackRequest(BaseModel):
    target_version_id: int
    reason: str = ""


class SkillRollbackResponse(BaseModel):
    ok: bool
    skill_id: int
    active_version: str
    rollback_from_version: str
    rollback_to_version: str
    version_id: int


# ---------------------------------------------------------------------------
# MCP server schemas
# ---------------------------------------------------------------------------

class MCPServerIn(BaseModel):
    server_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    transport: Literal["stdio", "http"]
    command: str = Field(default="", description="Shell command for stdio transport")
    base_url: str = Field(default="", description="HTTP base URL for http transport")
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    description: str = ""


class MCPServerOut(MCPServerIn):
    connected: bool = False
    tool_count: int = 0
    tools: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class MCPServerListResponse(BaseModel):
    servers: list[MCPServerOut]


class MCPServerRegisterResponse(BaseModel):
    ok: bool
    server_id: str
    tools: list[dict[str, Any]] = Field(default_factory=list)


class MCPServerDeleteResponse(BaseModel):
    ok: bool
    server_id: str


