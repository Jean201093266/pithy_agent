from __future__ import annotations

from typing import Any

from app.core.config_store import ModelConfig
from app.core.llm import LLMClient
from app.tools.registry import ToolRegistry

try:
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import StructuredTool
except Exception:  # pragma: no cover
    RunnableLambda = None
    StructuredTool = None


class LangChainAdapter:
    """Bridges existing local components into LangChain primitives."""

    def __init__(self, llm_client: LLMClient, tool_registry: ToolRegistry) -> None:
        self.llm_client = llm_client  # direct access for Planner/Executor
        self.tool_registry = tool_registry

    @property
    def available(self) -> bool:
        return RunnableLambda is not None

    def llm_runnable(self):
        if RunnableLambda is None:
            raise RuntimeError("langchain-core is not available")

        def _invoke(payload: dict[str, Any]) -> str:
            prompt = str(payload.get("prompt", ""))
            cfg = payload.get("cfg")
            if not isinstance(cfg, ModelConfig):
                raise ValueError("cfg(ModelConfig) is required")
            context = payload.get("context")
            if context is not None and not isinstance(context, list):
                context = []
            return self.llm_client.call(prompt, cfg, context)

        return RunnableLambda(_invoke)

    def build_structured_tools(self) -> dict[str, Any]:
        if StructuredTool is None:
            return {}

        out: dict[str, Any] = {}
        for tool_meta in self.tool_registry.list_tools():
            name = str(tool_meta.get("name", "")).strip()
            if not name:
                continue
            description = str(tool_meta.get("description", "")).strip() or f"Execute tool: {name}"

            def _run(__tool_name: str = name, **kwargs: Any) -> Any:
                return self.tool_registry.execute(__tool_name, kwargs, authorized=True)

            out[name] = StructuredTool.from_function(
                func=_run,
                name=name,
                description=description,
                infer_schema=True,
            )
        return out

    def execute_tool(self, tool_name: str, params: dict[str, Any]) -> Any:
        return self.tool_registry.execute(tool_name, params, authorized=True)

