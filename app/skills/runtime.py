from __future__ import annotations

from typing import Any

from app.core.config_store import ConfigStore
from app.core.db import AppDB
from app.core.llm import LLMClient
from app.tools.registry import ToolRegistry


class SkillRuntime:
    def __init__(self, db: AppDB, config_store: ConfigStore, llm: LLMClient, tools: ToolRegistry) -> None:
        self.db = db
        self.config_store = config_store
        self.llm = llm
        self.tools = tools

    def run(self, skill_id: int, input_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        skill = self.db.get_skill(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")

        spec = skill["spec"]
        outputs: list[dict[str, Any]] = []
        current_text = input_text

        for idx, step in enumerate(spec.get("steps", []), start=1):
            kind = step.get("kind")
            name = step.get("name")
            params = dict(step.get("params") or {})
            params.setdefault("input_text", current_text)
            params.update(context)

            if kind == "tool":
                result = self.tools.execute(name, params, authorized=True)
                outputs.append({"step": idx, "kind": kind, "name": name, "result": result})
                current_text = str(result)
            elif kind == "llm":
                cfg = self.config_store.get_model_config()
                prompt = params.get("prompt") or current_text
                result = self.llm.call(prompt, cfg, self.db.list_messages(limit=10))
                outputs.append({"step": idx, "kind": kind, "name": name, "result": result})
                current_text = result
            else:
                raise ValueError(f"unsupported skill step kind: {kind}")

        return {"skill": {"id": skill_id, "name": skill["name"]}, "output": current_text, "steps": outputs}

