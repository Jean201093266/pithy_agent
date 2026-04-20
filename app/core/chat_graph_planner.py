"""
Planner / Executor dual-agent architecture built on LangGraph StateGraph.

Graph flow
----------
  retrieve → plan → execute → synthesize → update → END

Node responsibilities
---------------------
* retrieve   – pull short-term + long-term memory context
* plan       – LLM decomposes the user request into a JSON step list
* execute    – iterates over plan steps; tool steps call ToolRegistry,
               LLM steps call the model directly (mini-ReAct per step)
* synthesize – merges all step outputs into a coherent final answer
* update     – persists messages and updates the memory store

The graph exposes a `.stream()` method that yields one snapshot dict per
completed node, which the SSE endpoint converts to typed events:
    {"type": "plan",       "steps": [...]}
    {"type": "step_start", "index": N, "task": "..."}
    {"type": "step_done",  "index": N, "output": "..."}
    {"type": "token",      "text": "..."}   (synthesize node chunks)
    {"type": "done",       ...}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Generator

from app.core.config_store import ModelConfig
from app.core.langchain_adapter import LangChainAdapter
from app.core.memory import MemoryManager
from app.core.llm_errors import LLMProviderError
from app.core.system_info import get_system_context_string
from app.tools.builtin import CommandNeedsConfirmation

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LangGraph imports (graceful fallback when not installed)
# ---------------------------------------------------------------------------
try:
    from langgraph.graph import END, StateGraph  # type: ignore
    _LANGGRAPH_OK = True
except Exception:
    END = "__end__"
    StateGraph = None
    _LANGGRAPH_OK = False

# Prompts – centralised in prompts.py
from app.core.prompts import PLANNER_PROMPT as _PLANNER_PROMPT
from app.core.prompts import SYNTHESIZE_PROMPT as _SYNTHESIZE_PROMPT


def _parse_plan(raw: str) -> dict[str, Any]:
    """Extract the JSON plan from the LLM output, handling markdown fences."""
    # strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\n?", "", raw).strip().rstrip("`").strip()
    # find first { ... }
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # fallback – single llm step
    return {
        "reasoning": "Could not parse plan; falling back to direct LLM response.",
        "steps": [{"index": 1, "task": raw[:200], "type": "llm", "tool": None, "params": {}}],
    }


class PlannerExecutorEngine:
    """
    Dual-agent graph engine.

    Parameters
    ----------
    adapter :
        The shared LangChain / LLM adapter.
    memory_manager :
        Memory retrieval and update manager.
    max_plan_steps : int
        Hard cap on the number of plan steps (default 6).
    """

    def __init__(
        self,
        adapter: LangChainAdapter,
        memory_manager: MemoryManager,
        max_plan_steps: int = 6,
    ) -> None:
        self.adapter = adapter
        self.memory_manager = memory_manager
        self.max_plan_steps = max_plan_steps
        self._graph = self._build() if _LANGGRAPH_OK and adapter.available else None

    @property
    def available(self) -> bool:
        return self._graph is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        message: str,
        cfg: ModelConfig,
        session_id: str,
        enabled_tools: list[dict[str, Any]],
        is_mock: bool = False,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        """Synchronous invoke – used by the non-streaming /api/chat endpoint."""
        if self._graph is None:
            raise RuntimeError("PlannerExecutorEngine: LangGraph not available")
        return self._graph.invoke(self._initial_state(
            message=message, cfg=cfg, session_id=session_id,
            enabled_tools=enabled_tools, is_mock=is_mock,
            system_prompt=system_prompt,
        ))

    def stream_events(
        self,
        *,
        message: str,
        cfg: ModelConfig,
        session_id: str,
        enabled_tools: list[dict[str, Any]],
        is_mock: bool = False,
        system_prompt: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        """
        Yield typed event dicts suitable for SSE serialisation.

        Event types emitted:
          plan        – plan was generated: {"steps": [...], "reasoning": "..."}
          step_start  – a step begins:      {"index": N, "task": "...", "type": "..."}
          step_done   – a step finished:    {"index": N, "output": "...", "tool": "..."}
          token       – streaming token:    {"text": "..."}
          done        – final:              {"session_id": ..., "session_name": ...,
                                             "token_usage": {...}}
          error       – {"message": "..."}
        """
        if self._graph is None:
            yield {"type": "error", "message": "PlannerExecutorEngine not available"}
            return

        init = self._initial_state(
            message=message, cfg=cfg, session_id=session_id,
            enabled_tools=enabled_tools, is_mock=is_mock,
            system_prompt=system_prompt,
        )

        try:
            # LangGraph .stream() yields {node_name: node_output_state} after each node
            for snapshot in self._graph.stream(init):
                for node_name, node_state in snapshot.items():
                    yield from self._snapshot_to_events(node_name, node_state)
        except LLMProviderError as exc:
            yield {"type": "error", "message": exc.message}
        except Exception as exc:
            LOGGER.exception("PlannerExecutorEngine.stream_events error")
            yield {"type": "error", "message": str(exc)}

    # ------------------------------------------------------------------
    # Graph builder
    # ------------------------------------------------------------------

    def _build(self):
        g = StateGraph(dict)
        g.add_node("retrieve",   self._node_retrieve)
        g.add_node("plan",       self._node_plan)
        g.add_node("execute",    self._node_execute)
        g.add_node("synthesize", self._node_synthesize)
        g.add_node("update",     self._node_update)

        g.set_entry_point("retrieve")
        g.add_edge("retrieve",   "plan")
        g.add_edge("plan",       "execute")
        g.add_edge("execute",    "synthesize")
        g.add_edge("synthesize", "update")
        g.add_edge("update",     END)
        return g.compile()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def _node_retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        session_id = str(state["session_id"])
        message    = str(state["message"])
        self.memory_manager.db.add_message("user", message, session_id=session_id)
        memory_ctx = self.memory_manager.retrieve_context(message, session_id=session_id)
        state["memory_ctx"]    = memory_ctx
        state["memory_prompt"] = str(memory_ctx.get("memory_prompt") or "")
        return state

    def _node_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        """Planner Agent: ask LLM to decompose task into structured steps."""
        message        = str(state["message"])
        cfg: ModelConfig = state["cfg"]
        is_mock        = bool(state.get("is_mock", False))
        enabled_tools  = state.get("enabled_tools") or []
        memory_prompt  = str(state.get("memory_prompt") or "无")

        tool_names = ", ".join(t["name"] for t in enabled_tools if t.get("enabled", True)) or "none"

        if is_mock:
            # Deterministic mock plan – skip LLM call
            plan_data: dict[str, Any] = {
                "reasoning": "mock plan",
                "steps": [{"index": 1, "task": message, "type": "llm", "tool": None, "params": {}}],
            }
        else:
            llm = self.adapter.llm_client
            planner_prompt = _PLANNER_PROMPT.format(
                tool_names=tool_names,
                memory_context=memory_prompt[:400],
                user_message=message,
                system_context=get_system_context_string(),
            )
            try:
                # Use a longer timeout for the planner call
                planner_cfg = ModelConfig(**{**cfg.__dict__})
                planner_cfg.timeout_seconds = max(cfg.timeout_seconds, 60)
                raw, usage = llm.call_with_usage(
                    planner_prompt, planner_cfg, context=None,
                    json_mode=True,  # structured output
                    system_prompt="You are a task planner. Always respond with valid JSON only.",
                )
                state["total_prompt_tokens"] = state.get("total_prompt_tokens", 0) + usage.prompt_tokens
                state["total_completion_tokens"] = state.get("total_completion_tokens", 0) + usage.completion_tokens
                plan_data = _parse_plan(str(raw))
            except Exception as exc:
                LOGGER.warning("Planner LLM failed: %s – using single-step fallback", exc)
                plan_data = {
                    "reasoning": f"Planner failed ({exc}), using direct response.",
                    "steps": [{"index": 1, "task": message, "type": "llm", "tool": None, "params": {}}],
                }

        # Clamp step count
        steps = plan_data.get("steps") or []
        steps = steps[:self.max_plan_steps]
        plan_data["steps"] = steps

        state["plan"]           = steps
        state["plan_reasoning"] = str(plan_data.get("reasoning") or "")
        state["step_outputs"]   = []
        state["react_trace"]    = []
        state["executed_results"] = []
        state["total_prompt_tokens"] = state.get("total_prompt_tokens", 0)
        state["total_completion_tokens"] = state.get("total_completion_tokens", 0)
        LOGGER.info("Plan generated: %d steps – %s", len(steps), plan_data.get("reasoning", "")[:100])
        return state

    def _node_execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Executor Agent: execute each plan step.

        - type == "tool"  → call ToolRegistry directly
        - type == "llm"   → call LLM with accumulated context
        """
        cfg: ModelConfig  = state["cfg"]
        is_mock           = bool(state.get("is_mock", False))
        enabled_tools     = state.get("enabled_tools") or []
        memory_prompt     = str(state.get("memory_prompt") or "")
        steps: list[dict] = state.get("plan") or []
        message           = str(state["message"])

        step_outputs: list[str]       = []
        executed_results: list[dict]  = []
        react_trace: list[dict]       = []
        llm = self.adapter.llm_client

        for step in steps:
            idx      = int(step.get("index", len(step_outputs) + 1))
            task     = str(step.get("task", ""))
            stype    = str(step.get("type", "llm"))
            tool_nm  = step.get("tool") or None
            s_params = step.get("params") or {}

            if stype == "tool" and tool_nm:
                # ── Tool execution step ──────────────────────────────
                # Allow the LLM to fill in missing params from context
                if not s_params and step_outputs:
                    context_summary = "\n".join(
                        f"Step {i+1} output: {o[:300]}" for i, o in enumerate(step_outputs)
                    )
                    s_params = {"input": context_summary}

                # Retry failed tool steps once
                max_tool_retries = 2
                result = None
                error = None
                output = ""
                for attempt in range(max_tool_retries):
                    try:
                        result = self.adapter.execute_tool(tool_nm, {k: str(v) for k, v in s_params.items()})
                        output = json.dumps(result, ensure_ascii=False)[:2000]
                        error = None
                        break
                    except CommandNeedsConfirmation:
                        # Auto-confirm: the planner already decided this command is needed
                        confirmed_params = {k: str(v) for k, v in s_params.items()}
                        confirmed_params["confirmed"] = "true"
                        try:
                            result = self.adapter.execute_tool(tool_nm, confirmed_params)
                            output = json.dumps(result, ensure_ascii=False)[:2000]
                            error = None
                        except Exception as exc2:
                            error = str(exc2)
                            output = f"[tool error after confirm] {exc2}"
                            result = {"error": str(exc2)}
                        break
                    except Exception as exc:
                        error = str(exc)
                        output = f"[tool error attempt {attempt+1}] {exc}"
                        result = {"error": str(exc)}
                        if attempt < max_tool_retries - 1:
                            import time as _time
                            _time.sleep(0.5)
                            LOGGER.warning("Tool %s step %d attempt %d failed: %s – retrying", tool_nm, idx, attempt+1, exc)

                executed_results.append({
                    "step": idx, "tool": tool_nm,
                    "params": s_params, "result": result,
                    "error": error,
                })
                react_trace.append({
                    "thought": f"Execute tool '{tool_nm}' for step {idx}: {task}",
                    "action": {"tool": tool_nm, "params": s_params},
                    "observation": result,
                })
                step_outputs.append(output)

            else:
                # ── LLM reasoning step ───────────────────────────────
                if is_mock:
                    output = f"[MockAgent] {task}"
                else:
                    prior_context = "\n".join(
                        f"Step {i+1}: {o[:400]}" for i, o in enumerate(step_outputs)
                    ) if step_outputs else ""
                    step_prompt = (
                        f"Runtime environment: {get_system_context_string()}\n"
                        f"User request: {message}\n"
                        + (f"Memory: {memory_prompt[:300]}\n" if memory_prompt else "")
                        + (f"Prior results:\n{prior_context}\n\n" if prior_context else "")
                        + f"Current task: {task}"
                    )
                    try:
                        raw_out, usage = llm.call_with_usage(
                            step_prompt, cfg, context=None,
                            system_prompt=state.get("system_prompt"),
                        )
                        output = str(raw_out)
                        state["total_prompt_tokens"] = state.get("total_prompt_tokens", 0) + usage.prompt_tokens
                        state["total_completion_tokens"] = state.get("total_completion_tokens", 0) + usage.completion_tokens
                    except LLMProviderError:
                        raise
                    except Exception as exc:
                        output = f"[llm step error] {exc}"

                react_trace.append({
                    "thought": f"LLM step {idx}: {task}",
                    "action": None,
                    "observation": {"output": output[:400]},
                })
                step_outputs.append(output)

        state["step_outputs"]     = step_outputs
        state["executed_results"] = executed_results
        state["react_trace"]      = react_trace
        state["last_result"]      = executed_results[-1]["result"] if executed_results else None
        return state

    def _node_synthesize(self, state: dict[str, Any]) -> dict[str, Any]:
        """Synthesize all step outputs into a final answer."""
        cfg: ModelConfig = state["cfg"]
        is_mock          = bool(state.get("is_mock", False))
        message          = str(state["message"])
        memory_prompt    = str(state.get("memory_prompt") or "")
        step_outputs     = state.get("step_outputs") or []
        react_trace      = state.get("react_trace") or []

        if is_mock:
            final = f"[MockAgent] Completed {len(step_outputs)} step(s): " + " | ".join(step_outputs)
        else:
            if not step_outputs:
                final = ""
            elif len(step_outputs) == 1 and not state.get("executed_results"):
                # Single LLM step – the output IS the answer
                final = step_outputs[0]
            else:
                trace_text = "\n".join(
                    f"Step {i+1}: {out[:600]}" for i, out in enumerate(step_outputs)
                )
                synth_prompt = _SYNTHESIZE_PROMPT.format(
                    user_message=message,
                    memory_context=memory_prompt[:300] or "none",
                    trace=trace_text,
                )
                llm = self.adapter.llm_client
                try:
                    raw_final, usage = llm.call_with_usage(
                        synth_prompt, cfg, context=None,
                        system_prompt=state.get("system_prompt"),
                    )
                    final = str(raw_final)
                    state["total_prompt_tokens"] = state.get("total_prompt_tokens", 0) + usage.prompt_tokens
                    state["total_completion_tokens"] = state.get("total_completion_tokens", 0) + usage.completion_tokens
                except LLMProviderError:
                    raise
                except Exception as exc:
                    final = "\n".join(step_outputs)  # graceful degradation
                    LOGGER.warning("Synthesize LLM failed: %s – using raw outputs", exc)

        state["final_reply"] = final
        state["total_prompt_tokens"] = state.get("total_prompt_tokens", 0)
        state["total_completion_tokens"] = state.get("total_completion_tokens", 0)
        return state

    def _node_update(self, state: dict[str, Any]) -> dict[str, Any]:
        """Persist assistant message and update memory."""
        session_id     = str(state["session_id"])
        final_reply    = str(state.get("final_reply") or "")
        message        = str(state["message"])
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _initial_state(
        *,
        message: str,
        cfg: ModelConfig,
        session_id: str,
        enabled_tools: list[dict[str, Any]],
        is_mock: bool,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        return {
            "message": message,
            "cfg": cfg,
            "session_id": session_id,
            "enabled_tools": enabled_tools,
            "is_mock": is_mock,
            "system_prompt": system_prompt,
            "memory_ctx": {},
            "memory_prompt": "",
            "plan": [],
            "plan_reasoning": "",
            "step_outputs": [],
            "executed_results": [],
            "react_trace": [],
            "last_result": None,
            "final_reply": "",
            "memory_update": {},
        }

    def _snapshot_to_events(
        self, node_name: str, node_state: dict[str, Any]
    ) -> Generator[dict[str, Any], None, None]:
        """Convert a single LangGraph node snapshot into SSE-ready event dicts."""
        if node_name == "retrieve":
            n = len((node_state.get("memory_ctx") or {}).get("long_term") or [])
            yield {"type": "step", "step": "memory",
                   "detail": f"记忆检索完成{'，召回 ' + str(n) + ' 条相关记忆' if n else ''}"}

        elif node_name == "plan":
            steps = node_state.get("plan") or []
            reasoning = node_state.get("plan_reasoning") or ""
            yield {
                "type": "plan",
                "reasoning": reasoning,
                "steps": [
                    {"index": s.get("index"), "task": s.get("task"), "type": s.get("type"),
                     "tool": s.get("tool")}
                    for s in steps
                ],
            }
            yield {"type": "step", "step": "think",
                   "detail": f"规划完成 ({len(steps)} 步): {reasoning[:120]}"}

        elif node_name == "execute":
            step_outputs = node_state.get("step_outputs") or []
            executed     = node_state.get("executed_results") or []
            plan         = node_state.get("plan") or []
            for i, output in enumerate(step_outputs):
                step_meta = plan[i] if i < len(plan) else {}
                task  = step_meta.get("task", f"步骤 {i+1}")
                stype = step_meta.get("type", "llm")
                tool  = step_meta.get("tool")
                yield {"type": "step_start", "index": i + 1, "task": task, "step_type": stype}
                if stype == "tool" and tool:
                    yield {"type": "step", "step": "tool", "detail": f"调用工具: {tool}"}
                yield {
                    "type": "step_done",
                    "index": i + 1,
                    "tool": tool,
                    "output": output[:500],
                }
                if stype == "tool":
                    yield {"type": "step", "step": "tool_done", "detail": f"工具 {tool} 执行完毕"}

        elif node_name == "synthesize":
            final = str(node_state.get("final_reply") or "")
            yield {"type": "step", "step": "answer", "detail": "正在合成最终回答…"}
            # Stream the final reply word-by-word
            words = final.split()
            for i, word in enumerate(words):
                yield {"type": "token", "text": ("" if i == 0 else " ") + word}

        elif node_name == "update":
            # Emit nothing here – the caller (main.py) emits the "done" event
            # after collecting session_name and token stats
            pass

