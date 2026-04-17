from __future__ import annotations

import json
import logging
from typing import Any

from app.core.agent import build_react_scratchpad, build_react_system_prompt, parse_react_llm_output
from app.core.config_store import ModelConfig
from app.core.langchain_adapter import LangChainAdapter
from app.core.memory import MemoryManager
from app.core.llm_errors import LLMProviderError

LOGGER = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover
    END = "__end__"
    StateGraph = None


def is_langgraph_available() -> bool:
    return StateGraph is not None


class ChatGraphEngine:
    """Graph-based retrieve -> reason -> update orchestration."""

    def __init__(self, adapter: LangChainAdapter, memory_manager: MemoryManager) -> None:
        self.adapter = adapter
        self.memory_manager = memory_manager
        self._graph = self._build_graph() if is_langgraph_available() and adapter.available else None

    @property
    def available(self) -> bool:
        return self._graph is not None

    def run(
        self,
        *,
        message: str,
        cfg: ModelConfig,
        session_id: str,
        force_tool: str | None,
        tool_params: dict[str, Any],
        enabled_tools: list[dict[str, Any]],
        is_mock: bool,
    ) -> dict[str, Any]:
        if self._graph is None:
            raise RuntimeError("langgraph is unavailable")

        state = {
            "message": message,
            "cfg": cfg,
            "session_id": session_id,
            "force_tool": force_tool,
            "tool_params": tool_params,
            "enabled_tools": enabled_tools,
            "is_mock": is_mock,
            "memory_ctx": {},
            "memory_prompt": "",
            "react_trace": [],
            "executed_results": [],
            "last_result": None,
            "final_reply": "",
            "memory_update": {},
        }
        return self._graph.invoke(state)

    def _build_graph(self):
        graph = StateGraph(dict)
        graph.add_node("retrieve", self._node_retrieve)
        graph.add_node("reason", self._node_reason)
        graph.add_node("update", self._node_update)

        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "reason")
        graph.add_edge("reason", "update")
        graph.add_edge("update", END)
        return graph.compile()

    def _node_retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        session_id = str(state["session_id"])
        message = str(state["message"])
        self.memory_manager.db.add_message("user", message, session_id=session_id)
        memory_ctx = self.memory_manager.retrieve_context(message, session_id=session_id)

        state["memory_ctx"] = memory_ctx
        state["memory_prompt"] = str(memory_ctx.get("memory_prompt") or "")
        return state

    def _node_reason(self, state: dict[str, Any]) -> dict[str, Any]:
        message = str(state["message"])
        cfg = state["cfg"]
        is_mock = bool(state["is_mock"])
        enabled_tools = state.get("enabled_tools") or []
        force_tool = state.get("force_tool")
        tool_params = state.get("tool_params") or {}
        memory_prompt = str(state.get("memory_prompt") or "")

        available_tool_names = {t.get("name", "") for t in enabled_tools}
        react_trace = state.get("react_trace") or []
        executed_results = state.get("executed_results") or []
        last_result = state.get("last_result")

        if force_tool:
            call_params = {k: str(v) for k, v in tool_params.items()}
            try:
                result = self.adapter.execute_tool(str(force_tool), call_params)
            except Exception as exc:
                result = {"error": str(exc)}
            executed_results.append({
                "tool": force_tool,
                "params": call_params,
                "reason": "force_tool",
                "result": result,
            })
            react_trace.append({
                "thought": f"User explicitly requested tool: {force_tool}",
                "action": {"tool": force_tool, "params": call_params},
                "observation": result,
            })
            last_result = result

        final_reply = ""
        llm = self.adapter.llm_runnable()
        if not is_mock:
            system_prompt = build_react_system_prompt(enabled_tools)
            max_steps = 6
            question = message if not memory_prompt else f"{message}\n\n[Memory Context]\n{memory_prompt}"
            for _ in range(max_steps):
                scratchpad = build_react_scratchpad(question, react_trace)
                try:
                    raw_output = llm.invoke({
                        "prompt": scratchpad,
                        "cfg": cfg,
                        "context": [{"role": "system", "content": system_prompt}],
                    })
                except LLMProviderError:
                    raise

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
                resolved_params: dict[str, Any] = {}
                for key, value in call.params.items():
                    if isinstance(value, str) and value.startswith("{{tool:") and value.endswith("}}"):
                        ref_name = value[7:-2]
                        ref = next((item for item in reversed(executed_results) if item["tool"] == ref_name), None)
                        resolved_params[key] = json.dumps(ref["result"], ensure_ascii=False) if ref else ""
                    else:
                        resolved_params[key] = value

                try:
                    result = self.adapter.execute_tool(call.name, resolved_params)
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

        if not final_reply:
            if executed_results:
                summary_prompt = (
                    f"用户输入: {message}\n"
                    f"记忆上下文: {memory_prompt or '无'}\n"
                    f"ReAct轨迹: {json.dumps(react_trace, ensure_ascii=False)}\n"
                    f"工具执行结果: {json.dumps(executed_results, ensure_ascii=False)}\n"
                    f"请根据以上信息给出最终回答。"
                )
            else:
                summary_prompt = message if not memory_prompt else f"{message}\n\n参考记忆:\n{memory_prompt}"
            context_messages = (state.get("memory_ctx") or {}).get("context_messages") or []
            final_reply = llm.invoke({"prompt": summary_prompt, "cfg": cfg, "context": context_messages})

        state["react_trace"] = react_trace
        state["executed_results"] = executed_results
        state["last_result"] = last_result
        state["final_reply"] = final_reply
        return state

    def _node_update(self, state: dict[str, Any]) -> dict[str, Any]:
        session_id = str(state["session_id"])
        final_reply = str(state.get("final_reply") or "")
        message = str(state.get("message") or "")
        executed_results = state.get("executed_results") or []

        self.memory_manager.db.add_message("assistant", final_reply, session_id=session_id)
        memory_update = self.memory_manager.update_after_turn(
            user_message=message,
            assistant_reply=final_reply,
            session_id=session_id,
            tool_trace=executed_results,
        )
        state["memory_update"] = memory_update
        return state


class ChatGraphEngineWithEnhancedMemory(ChatGraphEngine):
    """
    Extended ChatGraphEngine with hierarchical memory retrieval, ranking, and reflection.
    
    This subclass replaces the basic MemoryManager with EnhancedMemoryManager to provide:
    - Hierarchical context (core/summary/retrieval layers)
    - Composite importance scoring
    - Memory deduplication
    - Reflection mechanism
    - Dynamic retrieval strategies
    """
    
    def __init__(self, adapter: LangChainAdapter, memory_manager: MemoryManager, use_enhanced: bool = True) -> None:
        super().__init__(adapter, memory_manager)
        self.use_enhanced = use_enhanced
        
        if use_enhanced:
            self._init_enhanced_memory()
    
    def _init_enhanced_memory(self) -> None:
        """Initialize enhanced memory manager."""
        try:
            from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig
            
            self.enhanced_memory = EnhancedMemoryManager(
                self.memory_manager.db,
                config=EnhancedMemoryConfig(),
            )
            LOGGER.info("Enhanced memory system initialized")
        except ImportError:
            LOGGER.warning("Enhanced memory system not available, falling back to basic mode")
            self.enhanced_memory = None
            self.use_enhanced = False
    
    def _node_retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        """Retrieve context using enhanced memory if available."""
        session_id = str(state["session_id"])
        message = str(state["message"])
        self.memory_manager.db.add_message("user", message, session_id=session_id)
        
        if self.use_enhanced and self.enhanced_memory:
            # Use enhanced hierarchical retrieval
            memory_ctx = self.enhanced_memory.retrieve_context(
                message=message,
                session_id=session_id,
                complexity="medium",
            )
        else:
            # Fall back to basic retrieval
            memory_ctx = self.memory_manager.retrieve_context(message, session_id=session_id)
        
        state["memory_ctx"] = memory_ctx
        state["memory_prompt"] = str(memory_ctx.get("memory_prompt") or "")
        return state
    
    def _node_update(self, state: dict[str, Any]) -> dict[str, Any]:
        """Update memory using enhanced manager if available."""
        session_id = str(state["session_id"])
        final_reply = str(state.get("final_reply") or "")
        message = str(state.get("message") or "")
        executed_results = state.get("executed_results") or []
        
        self.memory_manager.db.add_message("assistant", final_reply, session_id=session_id)
        
        if self.use_enhanced and self.enhanced_memory:
            # Use enhanced update with reflection
            success = not any(
                word in final_reply.lower()
                for word in ["error", "fail", "unable", "无法", "错误"]
            )
            memory_update = self.enhanced_memory.update_after_turn(
                user_message=message,
                assistant_reply=final_reply,
                session_id=session_id,
                tool_trace=executed_results,
                success=success,
            )
        else:
            # Fall back to basic update
            memory_update = self.memory_manager.update_after_turn(
                user_message=message,
                assistant_reply=final_reply,
                session_id=session_id,
                tool_trace=executed_results,
            )
        
        state["memory_update"] = memory_update
        return state


def create_chat_graph_engine(
    adapter: LangChainAdapter,
    memory_manager: MemoryManager,
    use_enhanced_memory: bool = True,
) -> ChatGraphEngine:
    """
    Factory function to create appropriate ChatGraphEngine.
    
    Args:
        adapter: LangChainAdapter instance
        memory_manager: MemoryManager instance
        use_enhanced_memory: Whether to use enhanced memory system (default True)
    
    Returns:
        ChatGraphEngine or ChatGraphEngineWithEnhancedMemory
    """
    if use_enhanced_memory:
        return ChatGraphEngineWithEnhancedMemory(adapter, memory_manager, use_enhanced=True)
    return ChatGraphEngine(adapter, memory_manager)
