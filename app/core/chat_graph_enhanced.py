"""
Integration layer connecting enhanced memory system with LangGraph chat engine.

This module bridges EnhancedMemoryManager with the existing chat_graph.py to provide
hierarchical context retrieval, advanced ranking, and reflection capabilities.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.db import AppDB
from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig
from app.core.langchain_adapter import LangChainAdapter

LOGGER = logging.getLogger(__name__)


class ChatGraphWithEnhancedMemory:
    """
    Wrapper around ChatGraphEngine that uses EnhancedMemoryManager.

    This integrates with the existing langgraph state graph, replacing the
    basic MemoryManager with the advanced EnhancedMemoryManager.
    """

    def __init__(
        self,
        adapter: LangChainAdapter,
        db: AppDB,
        memory_config: EnhancedMemoryConfig | None = None,
    ):
        self.adapter = adapter
        self.db = db
        self.memory_manager = EnhancedMemoryManager(db, memory_config or EnhancedMemoryConfig())
        self._graph = None

        # Try to build langgraph
        try:
            from langgraph.graph import END, StateGraph
            self._graph_available = True
            self._StateGraph = StateGraph
            self._END = END
            if adapter.available:
                self._graph = self._build_graph()
        except Exception:  # pragma: no cover
            LOGGER.warning("LangGraph not available, graph mode disabled")
            self._graph_available = False
            self._StateGraph = None
            self._END = None

    @property
    def available(self) -> bool:
        """Check if graph mode is available."""
        return self._graph is not None and self._graph_available

    def retrieve_context_node(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        State node: Retrieve hierarchical memory context.

        Updates state with:
        - memory_ctx: full context data
        - memory_prompt: formatted prompt for LLM
        - context_complexity: estimated query complexity
        """
        session_id = str(state.get("session_id", "default"))
        message = str(state.get("message", ""))

        # Add user message to DB
        self.db.add_message("user", message, session_id=session_id)

        # Estimate query complexity
        complexity = self._estimate_complexity(message)

        # Retrieve hierarchical context
        memory_ctx = self.memory_manager.retrieve_context(
            message=message,
            session_id=session_id,
            complexity=complexity,
        )

        state["memory_ctx"] = memory_ctx
        state["memory_prompt"] = memory_ctx.get("memory_prompt", "")
        state["context_complexity"] = complexity

        LOGGER.debug(f"Retrieved context for session {session_id}: {memory_ctx.get('token_estimate', 0)} tokens")
        return state

    def reason_node(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        State node: ReAct reasoning with enhanced memory context.

        This node uses the hierarchical memory prompt to guide reasoning.
        """
        from app.core.agent import build_react_system_prompt, build_react_scratchpad, parse_react_llm_output

        message = str(state.get("message", ""))
        cfg = state.get("cfg")
        memory_prompt = str(state.get("memory_prompt", ""))
        enabled_tools = state.get("enabled_tools", [])
        force_tool = state.get("force_tool")
        tool_params = state.get("tool_params", {})
        is_mock = bool(state.get("is_mock", False))

        # Build system prompt with tools
        system_prompt = build_react_system_prompt(enabled_tools)

        # Inject hierarchical memory into system prompt
        if memory_prompt:
            from app.core.prompts import MEMORY_CONTEXT_INJECTION
            system_prompt = system_prompt + MEMORY_CONTEXT_INJECTION.format(memory_prompt=memory_prompt)

        # Build scratchpad from state
        react_trace = state.get("react_trace", [])
        executed_results = state.get("executed_results", [])
        scratchpad = build_react_scratchpad(react_trace, executed_results)

        # Compose full prompt
        user_input = f"{message}\n\n{scratchpad}".strip() if scratchpad else message

        # Call LLM
        response = self.adapter.chat(
            system_prompt=system_prompt,
            user_message=user_input,
            config=cfg,
            tools=enabled_tools,
            force_tool=force_tool,
            tool_params=tool_params,
            is_mock=is_mock,
        )

        # Parse response
        parsed = parse_react_llm_output(response, enabled_tools)

        state["last_llm_response"] = response
        state["last_parsed_decision"] = parsed.to_dict()

        return state

    def update_node(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        State node: Update memory after turn completion.

        Stores episodic memories, generates reflections, and prunes stale data.
        """
        session_id = str(state.get("session_id", "default"))
        message = str(state.get("message", ""))
        response = str(state.get("last_llm_response", ""))

        # Get tool trace from state
        tool_trace = state.get("executed_results", [])

        # Determine success (simple heuristic)
        success = not any(
            word in response.lower()
            for word in ["error", "fail", "unable", "无法"]
        )

        # Update memory with full turn information
        memory_update = self.memory_manager.update_after_turn(
            user_message=message,
            assistant_reply=response,
            session_id=session_id,
            tool_trace=tool_trace,
            success=success,
        )

        state["memory_update"] = memory_update
        state["memory_updated"] = True

        LOGGER.debug(
            f"Updated memory for session {session_id}: "
            f"added {memory_update.get('added_memory_items', 0)} items"
        )

        return state

    def _build_graph(self):
        """Build LangGraph state graph with enhanced memory nodes."""
        if not self._StateGraph or not self._END:
            return None

        graph = self._StateGraph(dict)

        # Add nodes in sequence
        graph.add_node("retrieve", self.retrieve_context_node)
        graph.add_node("reason", self.reason_node)
        graph.add_node("update", self.update_node)

        # Set up edges
        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "reason")
        graph.add_edge("reason", "update")
        graph.add_edge("update", self._END)

        return graph.compile()

    def run(
        self,
        *,
        message: str,
        cfg: Any,
        session_id: str,
        force_tool: str | None = None,
        tool_params: dict[str, Any] | None = None,
        enabled_tools: list[dict[str, Any]] | None = None,
        is_mock: bool = False,
    ) -> dict[str, Any]:
        """
        Execute the enhanced memory chat graph.

        Args:
            message: User message
            cfg: Model configuration
            session_id: Session identifier
            force_tool: Force specific tool usage
            tool_params: Tool parameters override
            enabled_tools: List of available tools
            is_mock: Mock mode for testing

        Returns:
            Final state dict with all processing results
        """
        if not self.available:
            raise RuntimeError("Graph mode not available (LangGraph missing or adapter unavailable)")

        state = {
            "message": message,
            "cfg": cfg,
            "session_id": session_id,
            "force_tool": force_tool,
            "tool_params": tool_params or {},
            "enabled_tools": enabled_tools or [],
            "is_mock": is_mock,
            # Memory nodes will populate these
            "memory_ctx": {},
            "memory_prompt": "",
            "context_complexity": "medium",
            "react_trace": [],
            "executed_results": [],
            "last_llm_response": "",
            "last_parsed_decision": {},
            "memory_update": {},
            "memory_updated": False,
        }

        # Execute graph
        result = self._graph.invoke(state)

        return result

    @staticmethod
    def _estimate_complexity(message: str) -> str:
        """Estimate query complexity to determine retrieval strategy."""
        msg_lower = message.lower()

        # Simple queries: short, direct questions
        if len(message) < 50 and message.count("?") == 1:
            return "simple"

        # Complex queries: multi-step, conditional logic
        if any(keyword in msg_lower for keyword in ["如何", "怎样", "解决", "实现", "构建"]):
            if len(message.split()) > 15:
                return "complex"

        # Multi-turn reasoning indicators
        if any(keyword in msg_lower for keyword in ["然后", "接下来", "之后", "步骤", "流程"]):
            return "complex"

        return "medium"


def create_enhanced_memory_graph(
    adapter: LangChainAdapter,
    db: AppDB,
    config: EnhancedMemoryConfig | None = None,
) -> ChatGraphWithEnhancedMemory:
    """
    Factory function to create enhanced memory chat graph.

    Args:
        adapter: LangChainAdapter instance
        db: AppDB instance
        config: Optional custom memory configuration

    Returns:
        ChatGraphWithEnhancedMemory instance
    """
    return ChatGraphWithEnhancedMemory(adapter, db, config)

