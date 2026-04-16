from __future__ import annotations

import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any

import psutil
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from app.core.llm import LLMClient
from app.core.llm_errors import LLMProviderError
from app.schemas import (
    ChatRequest,
    ChatResponse,
    AppSettingsIn,
    AppSettingsOut,
    AuthSessionResponse,
    LogsResponse,
    ModelConfigIn,
    ModelConfigOut,
    PasswordSetupRequest,
    ReleaseInfoResponse,
    SecurityStatusResponse,
    SkillRunRequest,
    SkillImportRequest,
    SkillImportResponse,
    SkillExportResponse,
    SkillVersionsResponse,
    SkillVersionItem,
    SkillRollbackRequest,
    SkillRollbackResponse,
    SkillSpec,
    ToolExecutionRequest,
    ToolImportResponse,
    ToolManifestIn,
    ToolManifestOut,
    ToolStatePatch,
    UnlockRequest,
)
from app.skills.runtime import SkillRuntime
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
tool_registry = ToolRegistry(db)
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
    return FileResponse(ROOT / "app" / "static" / "index.html")


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


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    _require_unlocked(request)
    language = detect_language(payload.message)
    db.add_message("user", payload.message)

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
            scratchpad = build_react_scratchpad(payload.message, react_trace)
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
                f"ReAct轨迹: {json.dumps(react_trace, ensure_ascii=False)}\n"
                f"工具执行结果: {json.dumps(executed_results, ensure_ascii=False)}\n"
                f"请根据以上信息给出最终回答。"
            )
        else:
            summary_prompt = payload.message
        try:
            final_reply = llm_client.call(summary_prompt, cfg, db.list_messages(limit=20))
        except LLMProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc

    db.add_message("assistant", final_reply)

    # Build a lightweight plan_exec dict for response compatibility
    plan_exec = build_light_plan_exec(payload.message)

    return ChatResponse(
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
        },
    )


@app.get("/api/history")
def history(request: Request) -> list[dict[str, Any]]:
    _require_unlocked(request)
    return db.list_messages(limit=100)


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

