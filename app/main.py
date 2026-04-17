from __future__ import annotations

import io
import json
import logging
import re
import secrets
import zipfile
from pathlib import Path
from typing import Any

import psutil
import yaml
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.agent import (
    build_light_plan_exec,
    build_react_scratchpad,
    build_react_system_prompt,
    detect_language,
    parse_react_llm_output,
    react_next_decision,
)
from app.core.config_store import AppSettings, ConfigStore, ModelConfig
from app.core.db import AppDB
from app.core.langchain_adapter import LangChainAdapter
from app.core.llm import LLMClient
from app.core.llm_errors import LLMProviderError
from app.core.chat_graph import ChatGraphEngine
from app.core.memory import MemoryManager
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
from app.tools.builtin import check_ocr_availability

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "agent.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

app = FastAPI(title="Pithy Local Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

db = AppDB(DATA_DIR / "agent.db")
config_store = ConfigStore(db, DATA_DIR / "secret.key")
llm_client = LLMClient()
memory_manager = MemoryManager(db)
tool_registry = ToolRegistry(db)
langchain_adapter = LangChainAdapter(llm_client, tool_registry)
chat_graph_engine = ChatGraphEngine(langchain_adapter, memory_manager)
skill_runtime = SkillRuntime(db, config_store, llm_client, tool_registry)
APP_LOGGER = logging.getLogger("pithy_agent")
AUTH_STATE: dict[str, Any] = {
    "locked": config_store.has_unlock_password(),
    "token": None,
    "failed_attempts": 0,
}


def _is_unlocked(request: Request | None = None) -> bool:
    if not config_store.has_unlock_password():
        return True
    if AUTH_STATE["locked"]:
        return False
    if request is None:
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
    return {
        "status": "ok",
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": vm.percent,
        "message_count": len(db.list_messages(limit=1000)),
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
    if config_store.has_unlock_password():
        raise HTTPException(status_code=400, detail="startup password already configured")
    config_store.set_unlock_password(payload.password)
    token = secrets.token_urlsafe(24)
    AUTH_STATE["token"] = token
    AUTH_STATE["locked"] = False
    AUTH_STATE["failed_attempts"] = 0
    _audit("password_setup")
    return AuthSessionResponse(ok=True, token=token, has_password=True, locked=False, failed_attempts=0)


@app.post("/api/security/unlock", response_model=AuthSessionResponse)
def unlock(payload: UnlockRequest) -> AuthSessionResponse:
    if not config_store.has_unlock_password():
        token = secrets.token_urlsafe(24)
        AUTH_STATE["token"] = token
        AUTH_STATE["locked"] = False
        return AuthSessionResponse(ok=True, token=token, has_password=False, locked=False, failed_attempts=0)
    if not config_store.verify_unlock_password(payload.password):
        AUTH_STATE["failed_attempts"] = int(AUTH_STATE["failed_attempts"]) + 1
        _audit("unlock_failed", f"attempts={AUTH_STATE['failed_attempts']}")
        raise HTTPException(status_code=401, detail="invalid password")
    token = secrets.token_urlsafe(24)
    AUTH_STATE["token"] = token
    AUTH_STATE["locked"] = False
    AUTH_STATE["failed_attempts"] = 0
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
    raw_session_id = (payload.session_id or "").strip()
    session_id = raw_session_id
    if not session_id:
        import secrets as _secrets
        import time as _time
        session_id = f"session_{int(_time.time() * 1000)}_{_secrets.token_hex(4)}"
        db.create_session(session_id, "新会话")
    else:
        known_sessions = {item["session_id"] for item in db.list_sessions()}
        if session_id not in known_sessions:
            db.create_session(session_id, session_id)
    language = detect_language(payload.message)

    cfg = config_store.get_model_config()
    is_mock = (cfg.provider or "mock").lower() == "mock"

    # Build available tools list for the prompt and parser
    all_tools = tool_registry.list_tools()
    enabled_tools = [t for t in all_tools if t.get("enabled", True)]
    available_tool_names: set[str] = {t["name"] for t in enabled_tools}

    react_trace: list[dict[str, Any]] = []
    executed_results: list[dict[str, Any]] = []
    last_result: Any | None = None
    final_reply: str = ""

    use_langgraph = chat_graph_engine.available
    if use_langgraph:
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

            plan_exec = build_light_plan_exec(payload.message)
            # Auto-generate title for the langgraph path too
            _lg_session_name = ""
            try:
                _sg_info = next((s for s in db.list_sessions() if s["session_id"] == session_id), None)
                if _sg_info:
                    _cn = _sg_info.get("name", "")
                    if _cn in {"新会话", session_id, "default", ""} or _cn.startswith("session_"):
                        _lg_session_name = _generate_session_title(session_id)
                        if _lg_session_name:
                            db.rename_session(session_id, _lg_session_name)
                    else:
                        _lg_session_name = _cn
            except Exception as _le:
                LOGGER.warning("lg auto title failed: %s", _le)
            return ChatResponse(
                session_id=session_id,
                session_name=_lg_session_name,
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
        except LLMProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
        except Exception:
            APP_LOGGER.exception("LangGraph chat path failed, falling back to legacy flow.")

    db.add_message("user", payload.message, session_id=session_id)
    memory_ctx = memory_manager.retrieve_context(payload.message, session_id=session_id)
    memory_prompt = str(memory_ctx.get("memory_prompt") or "").strip()

    MAX_STEPS = 6

    # ------------------------------------------------------------------ #
    # If a force_tool is specified, honour it immediately (first step)     #
    # ------------------------------------------------------------------ #
    if payload.force_tool:
        call_params = {k: str(v) for k, v in (payload.tool_params or {}).items()}
        try:
            result = tool_registry.execute(payload.force_tool, call_params, authorized=True)
        except Exception as exc:
            result = {"error": str(exc)}
        executed_results.append({
            "tool": payload.force_tool,
            "params": call_params,
            "reason": "force_tool",
            "result": result,
        })
        react_trace.append({
            "thought": f"User explicitly requested tool: {payload.force_tool}",
            "action": {"tool": payload.force_tool, "params": call_params},
            "observation": result,
        })
        last_result = result

    # ------------------------------------------------------------------ #
    # LLM-driven ReAct loop (skipped for mock provider to stay predictable)
    # ------------------------------------------------------------------ #
    if not is_mock:
        system_prompt = build_react_system_prompt(enabled_tools)

        for _step in range(MAX_STEPS):
            question = payload.message if not memory_prompt else f"{payload.message}\n\n[Memory Context]\n{memory_prompt}"
            scratchpad = build_react_scratchpad(question, react_trace)
            try:
                raw_output = llm_client.call(
                    scratchpad,
                    cfg,
                    context=[{"role": "system", "content": system_prompt}],
                )
            except LLMProviderError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc

            decision = parse_react_llm_output(raw_output, available_tool_names)

            if decision.should_stop or decision.action is None:
                final_reply = decision.final_answer or raw_output
                react_trace.append({
                    "thought": decision.thought,
                    "action": None,
                    "observation": {"stop_reason": decision.stop_reason or "final_answer"},
                })
                break

            call = decision.action
            # Resolve {{tool:xxx}} template references
            resolved_params: dict[str, Any] = {}
            for key, value in call.params.items():
                if isinstance(value, str) and value.startswith("{{tool:") and value.endswith("}}"):
                    ref_name = value[7:-2]
                    ref = next((item for item in reversed(executed_results) if item["tool"] == ref_name), None)
                    resolved_params[key] = json.dumps(ref["result"], ensure_ascii=False) if ref else ""
                else:
                    resolved_params[key] = value

            try:
                result = tool_registry.execute(call.name, resolved_params, authorized=True)
            except Exception as exc:
                result = {"error": str(exc)}

            executed_results.append({
                "tool": call.name,
                "params": resolved_params,
                "reason": call.reason,
                "result": result,
            })
            react_trace.append({
                "thought": decision.thought,
                "action": {"tool": call.name, "params": resolved_params},
                "observation": result,
            })
            last_result = result
        else:
            # Max steps reached – ask LLM to summarise
            react_trace.append({
                "thought": "Max steps reached.",
                "action": None,
                "observation": {"stop_reason": "max_steps_reached"},
            })

    # ------------------------------------------------------------------ #
    # Final LLM call: summarise with full context (or first call for mock) #
    # ------------------------------------------------------------------ #
    if not final_reply:
        if executed_results:
            summary_prompt = (
                f"用户输入: {payload.message}\n"
                f"记忆上下文: {memory_prompt or '无'}\n"
                f"ReAct轨迹: {json.dumps(react_trace, ensure_ascii=False)}\n"
                f"工具执行结果: {json.dumps(executed_results, ensure_ascii=False)}\n"
                f"请根据以上信息给出最终回答。"
            )
        else:
            summary_prompt = payload.message if not memory_prompt else f"{payload.message}\n\n参考记忆:\n{memory_prompt}"
        try:
            context_messages = memory_ctx.get("context_messages") or db.list_messages(limit=20, session_id=session_id)
            final_reply = llm_client.call(summary_prompt, cfg, context_messages)
        except LLMProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc

    db.add_message("assistant", final_reply, session_id=session_id)
    memory_update = memory_manager.update_after_turn(
        user_message=payload.message,
        assistant_reply=final_reply,
        session_id=session_id,
        tool_trace=executed_results,
    )

    # Build a lightweight plan_exec dict for response compatibility
    plan_exec = build_light_plan_exec(payload.message)

    # ── Auto-generate session title after first exchange ─────────────────
    session_name = ""
    try:
        session_info = next(
            (s for s in db.list_sessions() if s["session_id"] == session_id), None
        )
        if session_info:
            current_name = session_info.get("name", "")
            msg_count = int(session_info.get("message_count", 0))
            _auto_names = {"新会话", session_id, "default", ""}
            needs_title = current_name in _auto_names or current_name.startswith("session_")
            if needs_title and msg_count <= 4:
                session_name = _generate_session_title(session_id, cfg)
                if session_name:
                    db.rename_session(session_id, session_name)
            else:
                session_name = current_name
    except Exception as _e:
        LOGGER.warning("auto title generation failed: %s", _e)

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
            "strategy": "llm-react" if not is_mock else "mock-react",
            "react_trace": react_trace,
            "executed_tools": executed_results,
            "memory": {
                "session_id": session_id,
                "short_term_messages": len((memory_ctx.get("short_term") or {}).get("messages") or []),
                "retrieved_long_term": len(memory_ctx.get("long_term") or []),
                "summary": memory_update.get("summary") or "",
                "state": memory_update.get("state") or {},
            },
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
        LOGGER.warning("_generate_session_title error: %s", exc)
        return ""


@app.post("/api/sessions/{session_id}/generate-title")
def generate_session_title_api(session_id: str, request: Request) -> dict[str, Any]:
    """Manually trigger title generation for a session."""
    _require_unlocked(request)
    sessions = {s["session_id"] for s in db.list_sessions()}
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="session not found")
    cfg = config_store.get_model_config()
    title = _generate_session_title(session_id, cfg)
    if title:
        db.rename_session(session_id, title)
    _audit("session_title_generated", f"session_id={session_id} title={title!r}")
    return {"ok": True, "session_id": session_id, "name": title}




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
    lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    if level:
        level_upper = level.upper()
        lines = [line for line in lines if f" {level_upper} " in line]
    if search:
        needle = search.lower()
        lines = [line for line in lines if needle in line.lower()]
    redacted = [_redact_line(line) for line in lines[-limit:]]
    return LogsResponse(lines=redacted)

