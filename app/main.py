from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import zipfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
from typing import Any, Generator

import psutil
import yaml
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.core.agent import (
    build_light_plan_exec,
    detect_language,
)
from app.core.config_store import AppSettings, ConfigStore, ModelConfig
from app.core.db import AppDB
from app.core.langchain_adapter import LangChainAdapter
from app.core.llm import LLMClient
from app.core.llm_errors import LLMProviderError
from app.core.chat_graph import ChatGraphEngine
from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig
from app.core.input_guard import InputGuard
from app.schemas import (
    ChatRequest,
    ChatResponse,
    AppSettingsIn,
    AppSettingsOut,
    AuthSessionResponse,
    LogsResponse,
    MCPServerDeleteResponse,
    MCPServerIn,
    MCPServerListResponse,
    MCPServerOut,
    MCPServerRegisterResponse,
    ModelConfigIn,
    ModelConfigOut,
    PasswordSetupRequest,
    ReleaseInfoResponse,
    SecurityStatusResponse,
    SessionCreateRequest,
    SessionItem,
    SessionListResponse,
    SessionRenameRequest,
    SessionResponse,
    SkillRunRequest,
    SkillImportRequest,
    SkillImportResponse,
    SkillExportResponse,
    SkillVersionsResponse,
    SkillVersionItem,
    SkillRollbackRequest,
    SkillRollbackResponse,
    SkillSpec,
    SkillStatePatch,
    SkillPackageImportResponse,
    ToolExecutionRequest,
    ToolImportResponse,
    ToolManifestIn,
    ToolManifestOut,
    ToolStatePatch,
    UnlockRequest,
)
from app.skills.runtime import SkillRuntime
from app.tools.base import MCPServerConfig
from app.tools.registry import ToolRegistry
from app.tools.builtin import check_ocr_availability, CommandNeedsConfirmation

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class _JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        import time as _t
        entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "trace_id"):
            entry["trace_id"] = record.trace_id
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_log_handler = logging.FileHandler(LOG_DIR / "agent.log", encoding="utf-8")
_log_handler.setFormatter(_JSONFormatter())
_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(_log_level)
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
))

logging.basicConfig(level=_log_level, handlers=[_log_handler, _console_handler])
# Enable DEBUG for our own loggers (respect LOG_LEVEL if it's higher than DEBUG)
for _ln in ("pithy_agent", "pithy_agent.react", "pithy_agent.stream", "app.core.llm"):
    logging.getLogger(_ln).setLevel(_log_level if _log_level > logging.DEBUG else logging.DEBUG)

# ---------------------------------------------------------------------------
# Application lifespan: startup checks + graceful shutdown
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Run startup checks and clean up on shutdown."""
    _startup_logger = logging.getLogger("pithy_agent.lifecycle")
    _startup_logger.info("Starting Pithy Local Agent v%s", application.version)
    # Validate data directory
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Validate DB schema on startup
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1 FROM chat_sessions LIMIT 1")
        _startup_logger.info("Database schema validated")
    except Exception as exc:
        _startup_logger.error("Database validation failed: %s", exc)
    yield
    # Graceful shutdown
    _startup_logger.info("Shutting down – closing MCP connections")
    for sid in list(tool_registry._mcp_clients):
        try:
            tool_registry._disconnect_mcp_server(sid)
        except Exception:
            pass
    _startup_logger.info("Shutdown complete")


app = FastAPI(title="Pithy Local Agent", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000",
        "app://.",  # Electron origin
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from starlette.responses import JSONResponse as _JSONResponse

    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return _JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试。"})
except ImportError:
    limiter = None

# ---------------------------------------------------------------------------
# Global exception handler (return JSON, not HTML 500)
# ---------------------------------------------------------------------------
from starlette.responses import JSONResponse as _JSONResp


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    APP_LOGGER = logging.getLogger("pithy_agent")
    APP_LOGGER.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return _JSONResp(
        status_code=500,
        content={"detail": "服务器内部错误，请查看日志或稍后重试。", "type": type(exc).__name__},
    )

# ---------------------------------------------------------------------------
# Security headers middleware (pure ASGI to avoid SSE buffering issues)
# ---------------------------------------------------------------------------
from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Add security headers without buffering the response body (SSE-safe)."""

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"content-security-policy", (
            b"default-src 'self'; "
            b"script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            b"style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
            b"font-src 'self' https://fonts.gstatic.com; "
            b"img-src 'self' data:; "
            b"connect-src 'self'"
        )),
    ]

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self.HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestTraceMiddleware:
    """Inject a unique X-Trace-Id header into every response for observability."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = secrets.token_hex(8)
        scope.setdefault("state", {})["trace_id"] = trace_id

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestTraceMiddleware)

app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

db = AppDB(DATA_DIR / "agent.db")
config_store = ConfigStore(db, DATA_DIR / "secret.key")
llm_client = LLMClient()
memory_manager = EnhancedMemoryManager(db)
tool_registry = ToolRegistry(db)
langchain_adapter = LangChainAdapter(llm_client, tool_registry)
chat_graph_engine = ChatGraphEngine(langchain_adapter, memory_manager)
skill_runtime = SkillRuntime(db, config_store, llm_client, tool_registry)
APP_LOGGER = logging.getLogger("pithy_agent")
AUTH_STATE: dict[str, Any] = {
    "locked": config_store.has_unlock_password(),
    "token": None,
    "failed_attempts": 0,
    "lockout_until": 0.0,  # Unix timestamp
    "token_issued_at": 0.0,  # Unix timestamp
}

_TOKEN_MAX_AGE_SECONDS = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Shared helpers to reduce duplication between chat() and chat_stream()
# ---------------------------------------------------------------------------

def _ensure_session(session_id: str | None) -> str:
    """Ensure session exists, create if needed. Returns session_id."""
    import secrets as _secrets
    import time as _time
    sid = (session_id or "").strip()
    if not sid:
        sid = f"session_{int(_time.time() * 1000)}_{_secrets.token_hex(4)}"
        db.create_session(sid, "新会话")
    else:
        # Validate session_id format (alphanumeric, underscore, dash, max 120 chars)
        if len(sid) > 120 or not re.match(r'^[a-zA-Z0-9_\-]+$', sid):
            raise HTTPException(status_code=400, detail="invalid session_id format")
        # Use direct query instead of listing all sessions
        if not db.session_exists(sid):
            db.create_session(sid, sid)
    return sid


def _auto_title_session(session_id: str, cfg: Any = None) -> str:
    """Auto-generate session title if needed. Returns title or empty string."""
    try:
        with db.connect() as conn:
            row = conn.execute(
                """SELECT s.name, COUNT(c.id) AS message_count
                   FROM chat_sessions s
                   LEFT JOIN conversations c ON c.session_id = s.session_id
                   WHERE s.session_id = ?
                   GROUP BY s.session_id""",
                (session_id,),
            ).fetchone()
        if not row:
            return ""
        current_name = row["name"] or ""
        msg_count = int(row["message_count"])
        _auto_names = {"新会话", session_id, "default", ""}
        needs_title = current_name in _auto_names or current_name.startswith("session_")
        if needs_title and msg_count <= 4:
            title = _generate_session_title(session_id, cfg)
            if title:
                db.rename_session(session_id, title)
            return title
        return current_name
    except Exception as _e:
        APP_LOGGER.warning("auto title generation failed: %s", _e)
        return ""


def _is_unlocked(request: Request | None = None) -> bool:
    if not config_store.has_unlock_password():
        return True
    if AUTH_STATE["locked"]:
        return False
    if request is None:
        return False
    # Check token expiration
    import time as _t
    issued_at = AUTH_STATE.get("token_issued_at", 0.0)
    if issued_at and (_t.time() - issued_at) > _TOKEN_MAX_AGE_SECONDS:
        AUTH_STATE["locked"] = True
        AUTH_STATE["token"] = None
        return False
    return request.headers.get("X-Session-Token") == AUTH_STATE["token"]


def _require_unlocked(request: Request) -> None:
    if _is_unlocked(request):
        return
    raise HTTPException(status_code=423, detail="application locked")


def _audit(action: str, detail: str = "") -> None:
    APP_LOGGER.info("AUDIT %s %s", action, detail)


def _redact_line(text: str) -> str:
    masked = re.sub(r"(api[_-]?key|secret[_-]?key|password)\s*[=:]\s*([^\s,;]+)", r"\1=***", text, flags=re.IGNORECASE)
    masked = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer ***", masked, flags=re.IGNORECASE)
    return masked


@app.get("/")
def web_root() -> FileResponse:
    resp = FileResponse(ROOT / "app" / "static" / "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.get("/api/health")
def health() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    # Use COUNT query instead of fetching rows
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM conversations").fetchone()
            msg_count = int(row["cnt"]) if row else 0
    except Exception:
        msg_count = -1
    return {
        "status": "ok",
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": vm.percent,
        "message_count": msg_count,
    }


@app.get("/api/release/info", response_model=ReleaseInfoResponse)
def release_info() -> ReleaseInfoResponse:
    static_info_path = ROOT / "app" / "static" / "build-info.json"
    dist_publish_index = ROOT / "dist" / "publish-index.json"

    build_info: dict[str, Any] = {}
    if static_info_path.exists():
        try:
            build_info = json.loads(static_info_path.read_text(encoding="utf-8"))
        except Exception:
            build_info = {}

    publish_info: dict[str, Any] = {}
    if dist_publish_index.exists():
        try:
            publish_info = json.loads(dist_publish_index.read_text(encoding="utf-8"))
        except Exception:
            publish_info = {}

    signing = publish_info.get("signing") or build_info.get("signing") or {"status": "unknown", "isSigned": False}

    return ReleaseInfoResponse(
        name=str(build_info.get("name") or "pithy-agent"),
        product_name=str(build_info.get("productName") or "PithyLocalAgent"),
        version=str(build_info.get("version") or app.version),
        build_time=build_info.get("buildTime"),
        commit=build_info.get("commit"),
        channel=str(publish_info.get("channel") or "latest"),
        signing=signing,
    )


@app.get("/api/security/status", response_model=SecurityStatusResponse)
def security_status() -> SecurityStatusResponse:
    settings = config_store.get_app_settings()
    return SecurityStatusResponse(
        has_password=config_store.has_unlock_password(),
        locked=AUTH_STATE["locked"] if config_store.has_unlock_password() else False,
        failed_attempts=int(AUTH_STATE["failed_attempts"]),
        theme=settings.theme,
        language=settings.language,
    )


@app.post("/api/security/setup", response_model=AuthSessionResponse)
def security_setup(payload: PasswordSetupRequest) -> AuthSessionResponse:
    import time as _t
    if config_store.has_unlock_password():
        raise HTTPException(status_code=400, detail="startup password already configured")
    config_store.set_unlock_password(payload.password)
    token = secrets.token_urlsafe(24)
    AUTH_STATE["token"] = token
    AUTH_STATE["locked"] = False
    AUTH_STATE["failed_attempts"] = 0
    AUTH_STATE["token_issued_at"] = _t.time()
    _audit("password_setup")
    return AuthSessionResponse(ok=True, token=token, has_password=True, locked=False, failed_attempts=0)


@app.post("/api/security/unlock", response_model=AuthSessionResponse)
def unlock(payload: UnlockRequest, request: Request) -> AuthSessionResponse:
    import time as _t

    # Check lockout
    if AUTH_STATE.get("lockout_until", 0) and _t.time() < AUTH_STATE.get("lockout_until", 0):
        remaining = int(AUTH_STATE["lockout_until"] - _t.time())
        raise HTTPException(status_code=429, detail=f"账户已锁定，请 {remaining} 秒后重试")

    if not config_store.has_unlock_password():
        token = secrets.token_urlsafe(24)
        AUTH_STATE["token"] = token
        AUTH_STATE["locked"] = False
        AUTH_STATE["token_issued_at"] = _t.time()
        return AuthSessionResponse(ok=True, token=token, has_password=False, locked=False, failed_attempts=0)
    if not config_store.verify_unlock_password(payload.password):
        AUTH_STATE["failed_attempts"] = int(AUTH_STATE["failed_attempts"]) + 1
        _audit("unlock_failed", f"attempts={AUTH_STATE['failed_attempts']}")
        # Lockout after 5 failed attempts (60 seconds)
        if AUTH_STATE["failed_attempts"] >= 5:
            AUTH_STATE["lockout_until"] = _t.time() + 60
            _audit("account_lockout", f"locked for 60s after {AUTH_STATE['failed_attempts']} attempts")
        raise HTTPException(status_code=401, detail="invalid password")
    token = secrets.token_urlsafe(24)
    AUTH_STATE["token"] = token
    AUTH_STATE["locked"] = False
    AUTH_STATE["failed_attempts"] = 0
    AUTH_STATE["lockout_until"] = 0.0
    AUTH_STATE["token_issued_at"] = _t.time()
    _audit("unlock_success")
    return AuthSessionResponse(ok=True, token=token, has_password=True, locked=False, failed_attempts=0)


@app.post("/api/security/lock", response_model=AuthSessionResponse)
def lock(request: Request) -> AuthSessionResponse:
    _require_unlocked(request)
    if not config_store.has_unlock_password():
        return AuthSessionResponse(ok=True, token=None, has_password=False, locked=False, failed_attempts=0)
    AUTH_STATE["locked"] = True
    AUTH_STATE["token"] = None
    _audit("lock")
    return AuthSessionResponse(
        ok=True,
        token=None,
        has_password=config_store.has_unlock_password(),
        locked=True,
        failed_attempts=int(AUTH_STATE["failed_attempts"]),
    )


@app.get("/api/settings", response_model=AppSettingsOut)
def get_app_settings() -> AppSettingsOut:
    settings = config_store.get_app_settings()
    return AppSettingsOut(**settings.__dict__)


@app.put("/api/settings", response_model=AppSettingsOut)
def save_app_settings(payload: AppSettingsIn, request: Request) -> AppSettingsOut:
    _require_unlocked(request)
    settings = AppSettings(**payload.model_dump())
    settings.log_level = settings.log_level.upper()
    if not settings.system_prompt.strip():
        from app.core.config_store import _DEFAULT_SYSTEM_PROMPT
        settings.system_prompt = _DEFAULT_SYSTEM_PROMPT
    config_store.save_app_settings(settings)
    _audit("settings_updated", f"theme={settings.theme} language={settings.language}")
    return AppSettingsOut(**settings.__dict__)


@app.get("/api/config/model", response_model=ModelConfigOut)
def get_model_config(request: Request) -> ModelConfigOut:
    _require_unlocked(request)
    cfg = config_store.get_model_config()
    return ModelConfigOut(
        provider=cfg.provider,
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout_seconds=cfg.timeout_seconds,
        has_api_key=bool(cfg.api_key),
        has_secret_key=bool(cfg.secret_key),
    )


@app.put("/api/config/model", response_model=ModelConfigOut)
def save_model_config(payload: ModelConfigIn, request: Request) -> ModelConfigOut:
    _require_unlocked(request)
    cfg = ModelConfig(**payload.model_dump())
    config_store.save_model_config(cfg)
    _audit("model_config_updated", f"provider={cfg.provider} model={cfg.model}")
    return ModelConfigOut(
        provider=cfg.provider,
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout_seconds=cfg.timeout_seconds,
        context_window=cfg.context_window,
        has_api_key=bool(cfg.api_key),
        has_secret_key=bool(cfg.secret_key),
    )


@app.post("/api/config/model/test")
def test_model_config(request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    cfg = config_store.get_model_config()
    try:
        reply = llm_client.call("你好", cfg, [])
    except LLMProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "reply": reply}


@app.get("/api/tools")
def list_tools(request: Request) -> list[dict[str, Any]]:
    _require_unlocked(request)
    tools = tool_registry.list_tools()
    ocr_status = check_ocr_availability()
    for tool in tools:
        if tool["name"] == "ocr_image":
            tool["availability"] = ocr_status
    return tools


@app.get("/api/tools/ocr/status")
def ocr_status(request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    return check_ocr_availability()


@app.get("/api/tools/manifests", response_model=list[ToolManifestOut])
def list_tool_manifests(request: Request) -> list[ToolManifestOut]:
    _require_unlocked(request)
    return [ToolManifestOut(**item) for item in tool_registry.list_custom_manifests()]


@app.post("/api/tools/import", response_model=ToolImportResponse)
def import_tool_manifest(payload: ToolManifestIn, request: Request) -> ToolImportResponse:
    _require_unlocked(request)
    try:
        manifest = tool_registry.import_manifest(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit("tool_imported", f"tool={manifest.name} target={manifest.target_tool}")
    return ToolImportResponse(
        ok=True,
        tool=ToolManifestOut(**payload.model_dump(), source="custom"),
    )


@app.delete("/api/tools/custom/{tool_name}")
def delete_custom_tool(tool_name: str, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    try:
        tool_registry.delete_custom_tool(tool_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit("tool_deleted", f"tool={tool_name}")
    return {"ok": True, "deleted": tool_name}


@app.patch("/api/tools/{tool_name}")
def patch_tool_state(tool_name: str, payload: ToolStatePatch, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    try:
        tool_registry.set_enabled(tool_name, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tool not found") from exc
    _audit("tool_state_updated", f"tool={tool_name} enabled={payload.enabled}")
    return {"ok": True}


@app.post("/api/tools/{tool_name}/execute")
def execute_tool(tool_name: str, payload: ToolExecutionRequest, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    try:
        result = tool_registry.execute(tool_name, payload.params, payload.authorized)
    except CommandNeedsConfirmation as exc:
        return {"ok": False, "needs_confirmation": True, "command": exc.command, "reason": exc.reason}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tool not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit("tool_execute", f"tool={tool_name}")
    return {"ok": True, "result": result}


# ---------------------------------------------------------------------------
# MCP server management endpoints
# ---------------------------------------------------------------------------

@app.get("/api/mcp/servers", response_model=MCPServerListResponse)
def list_mcp_servers(request: Request) -> MCPServerListResponse:
    _require_unlocked(request)
    rows = tool_registry.list_mcp_servers()
    servers = [
        MCPServerOut(
            server_id=r["server_id"],
            transport=r["config"].get("transport", "http"),
            command=r["config"].get("command", ""),
            base_url=r["config"].get("base_url", ""),
            headers=r["config"].get("headers", {}),
            enabled=r["enabled"],
            description=r["config"].get("description", ""),
            connected=r.get("connected", False),
            tool_count=r.get("tool_count", 0),
            tools=r.get("tools", []),
            created_at=r.get("created_at", ""),
            updated_at=r.get("updated_at", ""),
        )
        for r in rows
    ]
    return MCPServerListResponse(servers=servers)


@app.post("/api/mcp/servers", response_model=MCPServerRegisterResponse)
def register_mcp_server(payload: MCPServerIn, request: Request) -> MCPServerRegisterResponse:
    _require_unlocked(request)
    if payload.transport == "stdio" and not payload.command.strip():
        raise HTTPException(status_code=400, detail="command is required for stdio transport")
    if payload.transport == "http" and not payload.base_url.strip():
        raise HTTPException(status_code=400, detail="base_url is required for http transport")
    cfg = MCPServerConfig(
        server_id=payload.server_id,
        transport=payload.transport,
        command=payload.command,
        base_url=payload.base_url,
        headers=payload.headers,
        enabled=payload.enabled,
        description=payload.description,
    )
    try:
        tools = tool_registry.register_mcp_server(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to register MCP server: {exc}") from exc
    _audit("mcp_server_registered", f"server_id={payload.server_id} tools={len(tools)}")
    return MCPServerRegisterResponse(ok=True, server_id=payload.server_id, tools=tools)


@app.post("/api/mcp/servers/{server_id}/refresh", response_model=MCPServerRegisterResponse)
def refresh_mcp_server(server_id: str, request: Request) -> MCPServerRegisterResponse:
    _require_unlocked(request)
    try:
        tools = tool_registry.refresh_mcp_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP server not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"refresh failed: {exc}") from exc
    _audit("mcp_server_refreshed", f"server_id={server_id}")
    return MCPServerRegisterResponse(ok=True, server_id=server_id, tools=tools)


@app.delete("/api/mcp/servers/{server_id}", response_model=MCPServerDeleteResponse)
def delete_mcp_server(server_id: str, request: Request) -> MCPServerDeleteResponse:
    _require_unlocked(request)
    deleted = tool_registry.unregister_mcp_server(server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="MCP server not found")
    _audit("mcp_server_deleted", f"server_id={server_id}")
    return MCPServerDeleteResponse(ok=True, server_id=server_id)


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    _require_unlocked(request)

    # ── Input guard ──────────────────────────────────────────────────────
    guard = InputGuard.check(payload.message)
    if guard.blocked:
        raise HTTPException(status_code=400, detail={"code": "INPUT_BLOCKED", "message": guard.reason})
    payload = ChatRequest(**{**payload.model_dump(), "message": guard.sanitised or payload.message})
    session_id = _ensure_session(payload.session_id)
    language = detect_language(payload.message)

    cfg = config_store.get_model_config()
    is_mock = (cfg.provider or "mock").lower() == "mock"

    # Build available tools list for the prompt and parser
    all_tools = tool_registry.list_tools()
    enabled_tools = [t for t in all_tools if t.get("enabled", True)]

    react_trace: list[dict[str, Any]] = []
    executed_results: list[dict[str, Any]] = []
    last_result: Any | None = None
    final_reply: str = ""

    try:
        graph_out = chat_graph_engine.run(
            message=payload.message,
            cfg=cfg,
            session_id=session_id,
            force_tool=payload.force_tool,
            tool_params=payload.tool_params,
            enabled_tools=enabled_tools,
            is_mock=is_mock,
        )
        react_trace = list(graph_out.get("react_trace") or [])
        executed_results = list(graph_out.get("executed_results") or [])
        last_result = graph_out.get("last_result")
        final_reply = str(graph_out.get("final_reply") or "")
        memory_update = dict(graph_out.get("memory_update") or {})
    except LLMProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc

    # Sanitize output to prevent XSS
    final_reply = InputGuard.sanitize_output(final_reply)

    # Build a lightweight plan_exec dict for response compatibility
    plan_exec = build_light_plan_exec(payload.message)

    # ── Auto-generate session title after first exchange ─────────────────
    session_name = _auto_title_session(session_id, cfg)

    return ChatResponse(
        session_id=session_id,
        session_name=session_name,
        language=language,
        plan=list(plan_exec.get("plan") or []),
        used_tool=executed_results[-1]["tool"] if executed_results else None,
        tool_result=last_result,
        reply=final_reply,
        brain={
            **plan_exec,
            "strategy": "langgraph-react",
            "react_trace": react_trace,
            "executed_tools": executed_results,
            "memory": {
                "session_id": session_id,
                "short_term_messages": len(((graph_out.get("memory_ctx") or {}).get("short_term") or {}).get("messages") or []),
                "retrieved_long_term": len((graph_out.get("memory_ctx") or {}).get("long_term") or []),
                "summary": memory_update.get("summary") or "",
                "state": memory_update.get("state") or {},
            },
        },
    )


# ---------------------------------------------------------------------------
# Streaming chat endpoint  POST /api/chat/stream
# Sends Server-Sent Events (SSE):
#   data: {"type":"step","step":"...","detail":"..."}   ← reasoning hint
#   data: {"type":"token","text":"..."}                 ← LLM token
#   data: {"type":"done","session_id":"...","session_name":"..."}
#   data: {"type":"error","message":"..."}
# ---------------------------------------------------------------------------

def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _sse_keepalive() -> str:
    """SSE comment used as a keepalive to prevent connection timeout."""
    return ": keepalive\n\n"


def _stream_with_heartbeat(gen_func, *args, heartbeat_interval: float = 8.0, **kwargs):
    """
    Wrap a blocking generator so that SSE keepalive comments are yielded
    while the generator is computing (no output for > heartbeat_interval seconds).
    This prevents browsers / reverse-proxies from dropping the connection.
    """
    import queue, threading, time as _t

    q: queue.Queue = queue.Queue()
    sentinel = object()

    def _producer():
        try:
            for item in gen_func(*args, **kwargs):
                q.put(item)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(sentinel)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    while True:
        try:
            item = q.get(timeout=heartbeat_interval)
        except queue.Empty:
            yield _sse_keepalive()
            continue
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def _run_react_streaming(
    message: str,
    cfg: ModelConfig,
    session_id: str,
    enabled_tools: list[dict[str, Any]],
    is_mock: bool,
    system_prompt: str,
    force_tool: str | None,
    tool_params: dict[str, Any] | None,
    mem_mgr: Any,
) -> Generator:
    """Run a ReAct loop yielding SSE event dicts for each step."""
    from app.core.agent import (
        build_react_system_prompt,
        build_react_scratchpad,
        parse_react_llm_output,
    )
    from app.tools.builtin import CommandNeedsConfirmation as _CNC
    _log = logging.getLogger("pithy_agent.react")

    _log.debug("[ReAct] session=%s message=%r force_tool=%s", session_id, message[:80], force_tool)

    mem_mgr.db.add_message("user", message, session_id=session_id)
    mem_ctx = mem_mgr.retrieve_context(message, session_id=session_id)
    mem_prompt = str(mem_ctx.get("memory_prompt") or "")
    _log.debug("[ReAct] memory retrieved: long_term=%d short_term=%d prompt_len=%d",
               len(mem_ctx.get("long_term") or []),
               len(((mem_ctx.get("short_term") or {}).get("messages") or [])),
               len(mem_prompt))

    yield {"type": "step", "step": "memory",
           "detail": f"记忆检索完成{'，召回 ' + str(len(mem_ctx.get('long_term') or [])) + ' 条' if mem_ctx.get('long_term') else ''}"}

    available_names = {t.get("name", "") for t in enabled_tools}
    _log.debug("[ReAct] available tools: %s", ", ".join(sorted(available_names)) or "none")
    react_trace: list[dict[str, Any]] = []
    executed_results: list[dict[str, Any]] = []

    # Handle force_tool
    if force_tool:
        params = {k: str(v) for k, v in (tool_params or {}).items()}
        _log.info("[ReAct] force_tool=%s params=%s", force_tool, params)
        yield {"type": "step", "step": "tool", "detail": f"调用工具: {force_tool}"}
        try:
            result = langchain_adapter.execute_tool(force_tool, params)
        except _CNC:
            _log.debug("[ReAct] force_tool %s needs confirmation, auto-confirming", force_tool)
            params["confirmed"] = "true"
            try:
                result = langchain_adapter.execute_tool(force_tool, params)
            except Exception as exc2:
                _log.warning("[ReAct] force_tool %s failed after confirm: %s", force_tool, exc2)
                result = {"error": str(exc2)}
        except Exception as exc:
            _log.warning("[ReAct] force_tool %s failed: %s", force_tool, exc)
            result = {"error": str(exc)}
        _log.debug("[ReAct] force_tool result: %s", json.dumps(result, ensure_ascii=False)[:200])
        executed_results.append({"tool": force_tool, "params": params, "result": result})
        react_trace.append({"thought": f"Force tool: {force_tool}", "action": {"tool": force_tool, "params": params}, "observation": result})
        yield {"type": "step_start", "index": 1, "task": f"执行 {force_tool}", "step_type": "tool"}
        yield {"type": "step_done", "index": 1, "tool": force_tool, "output": json.dumps(result, ensure_ascii=False)[:500]}
        yield {"type": "step", "step": "tool_done", "detail": f"工具 {force_tool} 执行完毕"}

    # ReAct loop (LLM decides whether to call tools)
    if not is_mock:
        react_system = build_react_system_prompt(enabled_tools)
        question = message if not mem_prompt else f"{message}\n\n[Memory Context]\n{mem_prompt}"
        max_steps = 6
        final_streamed = False
        _log.debug("[ReAct] entering loop, max_steps=%d, system_prompt_len=%d", max_steps, len(react_system))
        for step_i in range(max_steps):
            scratchpad = build_react_scratchpad(question, react_trace)
            _log.debug("[ReAct] step %d/%d scratchpad_len=%d", step_i + 1, max_steps, len(scratchpad))
            context = [{"role": "system", "content": react_system}]
            raw_output = llm_client.call(scratchpad, cfg, context=context, system_prompt=system_prompt)
            _log.debug("[ReAct] step %d LLM output (%d chars): %s", step_i + 1, len(raw_output), raw_output[:300])

            decision = parse_react_llm_output(raw_output, available_names)
            _log.debug("[ReAct] step %d decision: should_stop=%s action=%s stop_reason=%s thought=%s",
                       step_i + 1, decision.should_stop,
                       decision.action.name if decision.action else None,
                       decision.stop_reason, decision.thought[:80])

            if decision.should_stop or decision.action is None:
                react_trace.append({"thought": decision.thought, "action": None, "observation": {"stop_reason": decision.stop_reason}})
                final = decision.final_answer or raw_output
                # If this is the first step with no tools called, re-generate
                # with streaming for a smoother UX
                if step_i == 0 and not executed_results:
                    _log.debug("[ReAct] simple answer path — streaming direct LLM response")
                    yield {"type": "step", "step": "answer", "detail": "正在生成回答…"}
                    direct_prompt = message if not mem_prompt else f"{message}\n\n[Memory]\n{mem_prompt}"
                    context_msgs = mem_ctx.get("context_messages") or []
                    for chunk in llm_client.stream(direct_prompt, cfg, context=context_msgs, system_prompt=system_prompt):
                        yield {"type": "token", "text": chunk}
                else:
                    _log.debug("[ReAct] final answer after %d tool calls, chunking %d chars", len(executed_results), len(final))
                    yield {"type": "step", "step": "answer", "detail": "正在生成回答…"}
                    for chunk in _chunk_text(final):
                        yield {"type": "token", "text": chunk}
                final_streamed = True
                break

            # Tool call
            call = decision.action
            _log.info("[ReAct] step %d → tool call: %s params=%s", step_i + 1, call.name, call.params)
            yield {"type": "step_start", "index": step_i + 1, "task": decision.thought[:100], "step_type": "tool"}
            yield {"type": "step", "step": "tool", "detail": f"调用工具: {call.name}"}
            try:
                result = langchain_adapter.execute_tool(call.name, call.params)
            except _CNC:
                _log.debug("[ReAct] tool %s needs confirmation, auto-confirming", call.name)
                call.params["confirmed"] = "true"
                try:
                    result = langchain_adapter.execute_tool(call.name, call.params)
                except Exception as exc2:
                    _log.warning("[ReAct] tool %s failed after confirm: %s", call.name, exc2)
                    result = {"error": str(exc2)}
            except Exception as exc:
                _log.warning("[ReAct] tool %s failed: %s", call.name, exc)
                result = {"error": str(exc)}

            _log.debug("[ReAct] step %d tool result: %s", step_i + 1, json.dumps(result, ensure_ascii=False)[:300])
            executed_results.append({"tool": call.name, "params": call.params, "result": result})
            react_trace.append({"thought": decision.thought, "action": {"tool": call.name, "params": call.params}, "observation": result})
            yield {"type": "step_done", "index": step_i + 1, "tool": call.name, "output": json.dumps(result, ensure_ascii=False)[:500]}
            yield {"type": "step", "step": "tool_done", "detail": f"工具 {call.name} 执行完毕"}

        if not final_streamed:
            _log.debug("[ReAct] loop ended without final answer (max_steps reached or all tool calls)")
    else:
        # Mock mode
        _log.debug("[ReAct] mock mode")
        final_streamed = True
        final = f"[MockAgent] {message}"
        yield {"type": "step", "step": "answer", "detail": "Mock 回答"}
        yield {"type": "token", "text": final}

    # If no final answer was streamed yet (all steps were tool calls), generate summary
    if not is_mock and not final_streamed and react_trace and react_trace[-1].get("action") is not None:
        _log.info("[ReAct] generating summary after %d tool calls", len(executed_results))
        summary_prompt = (
            f"用户输入: {message}\n"
            f"记忆上下文: {mem_prompt or '无'}\n"
            f"工具执行结果: {json.dumps(executed_results, ensure_ascii=False)[:3000]}\n"
            f"请根据以上信息给出最终回答。"
        )
        yield {"type": "step", "step": "answer", "detail": "正在合成最终回答…"}
        for chunk in llm_client.stream(summary_prompt, cfg, system_prompt=system_prompt):
            yield {"type": "token", "text": chunk}

    # NOTE: Do NOT persist here — the caller has the assembled final_reply
    # and will save it after streaming completes.
    _log.debug("[ReAct] streaming complete, session=%s executed_tools=%d", session_id, len(executed_results))


def _chunk_text(text: str, chunk_size: int = 4) -> Generator:
    """Split text into small chunks for streaming, preserving newlines."""
    lines = text.split("\n")
    for li, line in enumerate(lines):
        if li > 0:
            yield "\n"
        words = line.split(" ")
        for wi, word in enumerate(words):
            yield ("" if wi == 0 else " ") + word


@app.post("/api/chat/stream")
def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    _require_unlocked(request)

    def generate():
        import secrets as _secrets, time as _time
        _log = logging.getLogger("pithy_agent.stream")
        _t0 = _time.time()

        # ── Input guard ───────────────────────────────────────────────
        _guard = InputGuard.check(payload.message)
        if _guard.blocked:
            _log.warning("[stream] input blocked: %s", _guard.reason)
            yield _sse({"type": "error", "message": _guard.reason})
            return
        _safe_message = _guard.sanitised or payload.message

        session_id = _ensure_session(payload.session_id)

        # ── Trace ID for this inference run ─────────────────────────────
        trace_id = _secrets.token_hex(8)
        _log.info("[stream] trace=%s session=%s msg=%r", trace_id, session_id, _safe_message[:80])
        yield _sse({"type": "trace", "trace_id": trace_id})

        language = detect_language(_safe_message)
        cfg = config_store.get_model_config()
        is_mock = (cfg.provider or "mock").lower() == "mock"
        settings = config_store.get_app_settings()
        system_prompt = settings.system_prompt
        _log.debug("[stream] provider=%s model=%s is_mock=%s tools_enabled=%d",
                   cfg.provider, cfg.model, is_mock, len([t for t in tool_registry.list_tools() if t.get("enabled", True)]))

        all_tools = tool_registry.list_tools()
        enabled_tools = [t for t in all_tools if t.get("enabled", True)]

        # ── ReAct engine: LLM autonomously decides tool usage ──────────
        yield _sse({"type": "step", "step": "think", "detail": "启动 ReAct 推理…"})

        final_reply = ""
        executed_results: list[dict[str, Any]] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        try:
            def _react_gen():
                yield from _run_react_streaming(
                    _safe_message, cfg, session_id, enabled_tools,
                    is_mock, system_prompt, payload.force_tool,
                    payload.tool_params, memory_manager,
                )
            for item in _stream_with_heartbeat(_react_gen):
                if isinstance(item, str):
                    yield item
                    continue
                evt = item
                if evt.get("type") == "token":
                    final_reply += evt.get("text", "")
                elif evt.get("type") == "step_done":
                    executed_results.append({
                        "step": evt.get("index"),
                        "tool": evt.get("tool"),
                        "output": evt.get("output"),
                    })
                yield _sse(evt)

        except LLMProviderError as exc:
            yield _sse({"type": "error", "message": exc.message})
            return

        # ── Persist assistant message with full content ──────────────────
        if final_reply:
            db.add_message("assistant", final_reply, session_id=session_id)
            memory_manager.update_after_turn(
                user_message=_safe_message,
                assistant_reply=final_reply,
                session_id=session_id,
                tool_trace=executed_results,
            )
            _log.debug("[stream] persisted assistant reply (%d chars) session=%s", len(final_reply), session_id)

        # ── Record token usage ───────────────────────────────────────────
        if total_prompt_tokens or total_completion_tokens:
            try:
                db.record_token_usage(
                    session_id=session_id,
                    trace_id=trace_id,
                    provider=cfg.provider or "unknown",
                    model=cfg.model or "unknown",
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )
            except Exception as _te:
                APP_LOGGER.warning("token usage record failed: %s", _te)

        # ── Auto-title ───────────────────────────────────────────────────
        session_name = _auto_title_session(session_id, cfg)

        yield _sse({
            "type": "done",
            "session_id": session_id,
            "session_name": session_name,
            "trace_id": trace_id,
            "token_usage": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            },
        })
        _log.info("[stream] trace=%s done in %.2fs tools=%d reply_len=%d",
                  trace_id, _time.time() - _t0, len(executed_results), len(final_reply))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

def _generate_session_title(session_id: str, cfg: "ModelConfig | None" = None) -> str:
    """Ask LLM to generate a short title (≤ 15 chars) based on the first few messages."""
    try:
        messages = db.list_messages(limit=6, session_id=session_id)
        if not messages:
            return ""
        snippet = "\n".join(
            f"{m['role']}: {m['content'][:120]}" for m in messages[:4]
        )
        prompt = (
            "请根据下面的对话，用5~12个汉字或英文单词生成一个简洁的会话标题，"
            "只输出标题本身，不要引号、标点、解释或其他内容。\n\n"
            f"{snippet}"
        )
        if cfg is None:
            cfg = config_store.get_model_config()
        title = llm_client.call(prompt, cfg, context=None)
        # Clean up: strip quotes, newlines, leading/trailing spaces
        title = title.strip().strip("'\"""''「」【】").strip()
        # Truncate to safe length
        return title[:20] if title else ""
    except Exception as exc:
        APP_LOGGER.warning("_generate_session_title error: %s", exc)
        return ""


@app.post("/api/sessions/{session_id}/generate-title")
def generate_session_title_api(session_id: str, request: Request) -> dict[str, Any]:
    """Manually trigger title generation for a session."""
    _require_unlocked(request)
    with db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="session not found")
    cfg = config_store.get_model_config()
    title = _generate_session_title(session_id, cfg)
    if title:
        db.rename_session(session_id, title)
    _audit("session_title_generated", f"session_id={session_id} title={title!r}")
    return {"ok": True, "session_id": session_id, "name": title}


@app.get("/api/sessions/{session_id}/stats")
def session_stats(session_id: str, request: Request) -> dict[str, Any]:
    """Return token usage statistics for a session."""
    _require_unlocked(request)
    stats = db.get_session_token_stats(session_id)
    return {"session_id": session_id, "token_usage": stats}


@app.get("/api/stats/tokens")
def global_token_stats(request: Request) -> dict[str, Any]:
    """Return aggregate token usage across all sessions."""
    _require_unlocked(request)
    return {"token_usage": db.get_global_token_stats()}




@app.get("/api/sessions/{session_id}/export")
def export_session(session_id: str, request: Request, format: str = "markdown") -> dict[str, Any]:
    """Export conversation as markdown or JSON."""
    _require_unlocked(request)
    with db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="session not found")
    messages = db.list_messages(limit=500, session_id=session_id)
    if format == "json":
        content = json.dumps(messages, ensure_ascii=False, indent=2)
        filename = f"session-{session_id[:8]}.json"
    else:
        lines = [f"# 对话记录\n\n**会话 ID**: `{session_id}`\n"]
        for m in messages:
            role_label = "**用户**" if m["role"] == "user" else "**AI**"
            ts = m.get("created_at", "")
            lines.append(f"\n---\n{role_label} · {ts}\n\n{m['content']}\n")
        content = "\n".join(lines)
        filename = f"session-{session_id[:8]}.md"
    return {"content": content, "filename": filename, "format": format}


@app.get("/api/history")
def history(request: Request, session_id: str = Query(default="")) -> list[dict[str, Any]]:
    _require_unlocked(request)
    sid = (session_id or "").strip()
    if not sid:
        return []
    return db.list_messages(limit=200, session_id=sid)


# ---------------------------------------------------------------------------
# Chat session management endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions", response_model=SessionListResponse)
def list_sessions(request: Request) -> SessionListResponse:
    _require_unlocked(request)
    rows = db.list_sessions()
    return SessionListResponse(sessions=[SessionItem(**r) for r in rows])


@app.post("/api/sessions", response_model=SessionResponse)
def create_session(payload: SessionCreateRequest, request: Request) -> SessionResponse:
    _require_unlocked(request)
    import secrets as _secrets
    import time as _time
    sid = (payload.session_id or "").strip()
    if not sid:
        sid = f"session_{int(_time.time() * 1000)}_{_secrets.token_hex(4)}"
    name = (payload.name or "").strip() or sid
    db.create_session(sid, name)
    _audit("session_create", f"session_id={sid}")
    return SessionResponse(ok=True, session_id=sid, name=name)


@app.patch("/api/sessions/{session_id}", response_model=SessionResponse)
def rename_session(session_id: str, payload: SessionRenameRequest, request: Request) -> SessionResponse:
    _require_unlocked(request)
    ok = db.rename_session(session_id, payload.name)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    _audit("session_rename", f"session_id={session_id} name={payload.name}")
    return SessionResponse(ok=True, session_id=session_id, name=payload.name)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    ok = db.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    _audit("session_delete", f"session_id={session_id}")
    return {"ok": True, "session_id": session_id}


@app.get("/api/skills")
def list_skills(request: Request) -> list[dict[str, Any]]:
    _require_unlocked(request)
    return db.list_skills()


@app.post("/api/skills")
def upsert_skill(payload: SkillSpec, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    skill_id = db.upsert_skill(payload.name, payload.version, payload.model_dump())
    return {"ok": True, "id": skill_id}


def _parse_skill_content(content: str, fmt: str) -> tuple[dict[str, Any], str]:
    if fmt == "json":
        return json.loads(content), "json"
    if fmt == "yaml":
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ValueError("yaml content must be an object")
        return parsed, "yaml"

    try:
        return json.loads(content), "json"
    except json.JSONDecodeError:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ValueError("content must be valid json/yaml object")
        return parsed, "yaml"


@app.post("/api/skills/import", response_model=SkillImportResponse)
def import_skill(payload: SkillImportRequest, request: Request) -> SkillImportResponse:
    _require_unlocked(request)
    try:
        parsed, imported_format = _parse_skill_content(payload.content, payload.format)
        spec = SkillSpec.model_validate(parsed)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid skill content: {exc}") from exc

    skill_id = db.upsert_skill(spec.name, spec.version, spec.model_dump(), source="import")
    return SkillImportResponse(
        ok=True,
        skill_id=skill_id,
        name=spec.name,
        version=spec.version,
        imported_format=imported_format,
    )


@app.get("/api/skills/{skill_id}/export", response_model=SkillExportResponse)
def export_skill(skill_id: int, request: Request, format: str = "json", version_id: int | None = None) -> SkillExportResponse:
    _require_unlocked(request)
    fmt = format.lower()
    if fmt not in {"json", "yaml"}:
        raise HTTPException(status_code=400, detail="format must be json or yaml")

    skill = db.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")

    name = skill["name"]
    version = skill["version"]
    spec = skill["spec"]
    if version_id is not None:
        version_obj = db.get_skill_version(skill_id, version_id)
        if version_obj is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        name = version_obj["name"]
        version = version_obj["version"]
        spec = version_obj["spec"]

    if fmt == "json":
        content = json.dumps(spec, ensure_ascii=False, indent=2)
    else:
        content = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)

    return SkillExportResponse(
        ok=True,
        skill_id=skill_id,
        name=name,
        version=version,
        format=fmt,
        content=content,
    )


@app.get("/api/skills/{skill_id}/versions", response_model=SkillVersionsResponse)
def list_skill_versions(skill_id: int, request: Request) -> SkillVersionsResponse:
    _require_unlocked(request)
    skill = db.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    versions = db.list_skill_versions(skill_id)
    return SkillVersionsResponse(
        skill_id=skill_id,
        name=skill["name"],
        versions=[
            SkillVersionItem(
                version_id=v["version_id"],
                version=v["version"],
                source=v["source"],
                created_at=v["created_at"],
            )
            for v in versions
        ],
    )


@app.post("/api/skills/{skill_id}/rollback", response_model=SkillRollbackResponse)
def rollback_skill(skill_id: int, payload: SkillRollbackRequest, request: Request) -> SkillRollbackResponse:
    _require_unlocked(request)
    try:
        result = db.rollback_skill(skill_id, payload.target_version_id, payload.reason)
    except KeyError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    active = db.get_skill(skill_id)
    if active is None:
        raise HTTPException(status_code=404, detail="skill not found")

    return SkillRollbackResponse(
        ok=True,
        skill_id=skill_id,
        active_version=active["version"],
        rollback_from_version=result["rollback_from_version"],
        rollback_to_version=result["rollback_to_version"],
        version_id=result["version_id"],
    )


@app.post("/api/skills/{skill_id}/run")
def run_skill(skill_id: int, payload: SkillRunRequest, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    try:
        return skill_runtime.run(skill_id, payload.input_text, payload.context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/skills/{skill_id}")
def patch_skill_state(skill_id: int, payload: SkillStatePatch, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    ok = db.set_skill_enabled(skill_id, payload.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="skill not found")
    _audit("skill_state_updated", f"skill_id={skill_id} enabled={payload.enabled}")
    return {"ok": True, "skill_id": skill_id, "enabled": payload.enabled}


@app.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: int, request: Request) -> dict[str, Any]:
    _require_unlocked(request)
    skill = db.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    ok = db.delete_skill(skill_id)
    if not ok:
        raise HTTPException(status_code=404, detail="skill not found")
    _audit("skill_deleted", f"skill_id={skill_id} name={skill['name']}")
    return {"ok": True, "deleted": skill_id}


@app.post("/api/skills/import/package", response_model=SkillPackageImportResponse)
async def import_skill_package(request: Request, file: UploadFile = File(...)) -> SkillPackageImportResponse:
    """Import a zip package containing one or more skill JSON/YAML files."""
    _require_unlocked(request)
    content = await file.read()
    imported_skills: list[dict] = []
    errors: list[str] = []

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if not (name.endswith(".json") or name.endswith(".yaml") or name.endswith(".yml")):
                    continue
                try:
                    raw = zf.read(name).decode("utf-8")
                    fmt = "yaml" if name.endswith((".yaml", ".yml")) else "json"
                    parsed, _ = _parse_skill_content(raw, fmt)
                    spec = SkillSpec.model_validate(parsed)
                    skill_id = db.upsert_skill(spec.name, spec.version, spec.model_dump(), source="package_import")
                    imported_skills.append({"id": skill_id, "name": spec.name, "version": spec.version, "file": name})
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"invalid zip file: {exc}") from exc

    if not imported_skills and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    _audit("skill_package_imported", f"count={len(imported_skills)}")
    return SkillPackageImportResponse(ok=True, imported=len(imported_skills), skills=imported_skills)


@app.get("/api/logs")
def logs(
    request: Request,
    limit: int = Query(default=120, ge=20, le=500),
    level: str = Query(default=""),
    search: str = Query(default=""),
) -> LogsResponse:
    _require_unlocked(request)
    log_file = LOG_DIR / "agent.log"
    if not log_file.exists():
        return LogsResponse(lines=[])
    # Read only the tail of the file to avoid loading huge log files
    from collections import deque
    max_scan = limit * 5  # scan extra lines to account for filtering
    tail: deque[str] = deque(maxlen=max_scan)
    with open(log_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            tail.append(line.rstrip("\n"))
    lines = list(tail)
    if level:
        level_upper = level.upper()
        lines = [line for line in lines if level_upper in line]
    if search:
        needle = search.lower()
        lines = [line for line in lines if needle in line.lower()]
    redacted = [_redact_line(line) for line in lines[-limit:]]
    return LogsResponse(lines=redacted)

