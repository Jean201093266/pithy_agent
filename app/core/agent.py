from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from langdetect import detect

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ReAct system prompt template
# ---------------------------------------------------------------------------
_REACT_SYSTEM_TMPL = """\
You are a helpful local agent that follows the ReAct (Reasoning + Acting) protocol strictly.

Available tools:
{tool_descriptions}

Output format – repeat until you reach a final answer:
Thought: <your reasoning>
Action: <tool name, must be one of the available tools listed above>
Action Input: <JSON object with the tool parameters>
Observation: <tool result will be appended here by the system>

When you have enough information to answer the user, output:
Thought: <final reasoning>
Final Answer: <your response to the user>

Rules:
- Always start with "Thought:".
- "Action:" must be followed by exactly one tool name.
- "Action Input:" must be a valid JSON object.
- Do NOT fabricate Observations; wait for the system.
- If no tool is needed, go straight to "Final Answer:".
"""


def detect_language(text: str) -> str:
    try:
        return detect(text)
    except Exception:
        return "unknown"


@dataclass
class AgentToolCall:
    name: str
    params: dict[str, str] = field(default_factory=dict)
    reason: str = ""


@dataclass
class AgentBrainResult:
    language: str
    intent: str
    plan: list[str]
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "intent": self.intent,
            "plan": self.plan,
            "tool_calls": [asdict(item) for item in self.tool_calls],
            "confidence": self.confidence,
        }


@dataclass
class ReActDecision:
    thought: str
    action: AgentToolCall | None
    should_stop: bool = False
    stop_reason: str = ""
    final_answer: str = ""


# ---------------------------------------------------------------------------
# LLM-driven ReAct helpers
# ---------------------------------------------------------------------------

def build_react_system_prompt(tools: list[dict[str, Any]]) -> str:
    """Build the ReAct system prompt with the list of available tools."""
    if tools:
        lines = []
        for t in tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            params = t.get("parameters", {})
            param_str = ", ".join(params.keys()) if isinstance(params, dict) else ""
            lines.append(f"- {name}({param_str}): {desc}")
        tool_descriptions = "\n".join(lines)
    else:
        tool_descriptions = "(no tools available)"
    return _REACT_SYSTEM_TMPL.format(tool_descriptions=tool_descriptions)


def build_react_scratchpad(message: str, trace: list[dict[str, Any]]) -> str:
    """Build the full ReAct scratchpad from the user message and previous trace steps."""
    parts = [f"Question: {message}"]
    for step in trace:
        thought = step.get("thought", "")
        action_info = step.get("action")
        observation = step.get("observation")
        if thought:
            parts.append(f"Thought: {thought}")
        if action_info:
            tool_name = action_info.get("tool", "")
            params = action_info.get("params", {})
            parts.append(f"Action: {tool_name}")
            parts.append(f"Action Input: {json.dumps(params, ensure_ascii=False)}")
        if observation is not None:
            obs_str = json.dumps(observation, ensure_ascii=False) if not isinstance(observation, str) else observation
            parts.append(f"Observation: {obs_str}")
    return "\n".join(parts)


def parse_react_llm_output(text: str, available_tools: set[str] | None = None) -> ReActDecision:
    """
    Parse LLM output following the standard ReAct text protocol.

    Expected patterns (case-insensitive labels):
        Thought: ...
        Action: <tool_name>
        Action Input: <json>

        or

        Thought: ...
        Final Answer: ...
    """
    text = text.strip()

    # Extract Thought (first occurrence)
    thought_match = re.search(r"(?i)^thought\s*:\s*(.+?)(?=\n(?:action|final answer)\s*:|$)", text, re.DOTALL | re.MULTILINE)
    thought = thought_match.group(1).strip() if thought_match else ""

    # Check for Final Answer
    final_match = re.search(r"(?i)final answer\s*:\s*(.+)", text, re.DOTALL)
    if final_match:
        return ReActDecision(
            thought=thought,
            action=None,
            should_stop=True,
            stop_reason="final_answer",
            final_answer=final_match.group(1).strip(),
        )

    # Check for Action + Action Input
    action_match = re.search(r"(?i)^action\s*:\s*(.+)$", text, re.MULTILINE)
    action_input_match = re.search(r"(?i)^action input\s*:\s*(.+?)(?=\nthought\s*:|$)", text, re.DOTALL | re.MULTILINE)

    if action_match:
        tool_name = action_match.group(1).strip()
        params: dict[str, Any] = {}
        if action_input_match:
            raw_input = action_input_match.group(1).strip()
            # Try JSON parse first
            try:
                parsed = json.loads(raw_input)
                if isinstance(parsed, dict):
                    params = {k: str(v) for k, v in parsed.items()}
                else:
                    params = {"input": str(parsed)}
            except (json.JSONDecodeError, ValueError):
                # Treat as plain string input
                params = {"input": raw_input}

        # Validate tool name (optional guard)
        if available_tools and tool_name not in available_tools:
            LOGGER.warning("ReAct parser: LLM requested unknown tool '%s'", tool_name)

        return ReActDecision(
            thought=thought,
            action=AgentToolCall(name=tool_name, params=params, reason="llm_react"),
        )

    # LLM produced unstructured text — treat as final answer
    return ReActDecision(
        thought=thought or text,
        action=None,
        should_stop=True,
        stop_reason="unstructured_output",
        final_answer=text,
    )


# ---------------------------------------------------------------------------
# Legacy heuristic helpers (kept as fallback / for tests)
# ---------------------------------------------------------------------------

def _extract_file_path(text: str) -> str | None:
    quoted = re.search(r'"([A-Za-z]:\\[^\"]+|[^\"]+\.[A-Za-z0-9]+)"', text)
    if quoted:
        return quoted.group(1)
    path_match = re.search(r'([A-Za-z]:\\[^\s]+|[^\s]+\.(txt|md|json|yaml|yml|csv|log))', text)
    if path_match:
        return path_match.group(1)
    return None


def _extract_search_query(text: str) -> str:
    patterns = [r"search\s+(for\s+)?(.+)", r"搜索(一下|关于)?(.+)"]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.groups()[-1].strip(" ：:，,")
            if candidate:
                return candidate
    return text.strip()


def _extract_write_content(text: str) -> str | None:
    match = re.search(r"(?:写入|保存|write|save)(?:到文件)?[:： ]+(.+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def build_plan(text: str) -> AgentBrainResult:
    lower = text.lower()
    language = detect_language(text)
    file_path = _extract_file_path(text)
    search_query = _extract_search_query(text)
    write_content = _extract_write_content(text)

    wants_search = "搜索" in text or "search" in lower
    wants_read = "读取" in text or "read file" in lower or "打开文件" in text
    wants_write = "写入" in text or "保存" in text or "write file" in lower or "save to" in lower
    wants_json = "json" in lower and ("解析" in text or "parse" in lower)
    wants_command = "命令" in text or "command" in lower or "运行程序" in text or "run command" in lower

    tool_calls: list[AgentToolCall] = []
    plan: list[str] = []
    intent = "chat"
    confidence = 0.45

    if wants_search and wants_write and file_path:
        intent = "search_and_save"
        confidence = 0.92
        plan = ["解析搜索主题", "调用 web_search 获取资料", "将结果写入指定文件", "生成最终回复"]
        tool_calls = [
            AgentToolCall(name="web_search", params={"query": search_query}, reason="根据用户需求先检索信息"),
            AgentToolCall(name="write_file", params={"path": file_path, "content": "{{tool:web_search}}"}, reason="将搜索结果写入文件"),
        ]
    elif wants_read and file_path:
        intent = "read_file"
        confidence = 0.9
        plan = ["提取文件路径", "调用 read_file", "总结文件内容"]
        tool_calls = [AgentToolCall(name="read_file", params={"path": file_path}, reason="用户请求读取文件" )]
    elif wants_write and file_path:
        intent = "write_file"
        confidence = 0.88
        plan = ["提取文件路径和内容", "调用 write_file", "确认写入结果"]
        tool_calls = [
            AgentToolCall(
                name="write_file",
                params={"path": file_path, "content": write_content or text},
                reason="用户请求将内容写入文件",
            )
        ]
    elif wants_search:
        intent = "search"
        confidence = 0.85
        plan = ["解析搜索需求", "调用 web_search", "整理结果并回复"]
        tool_calls = [AgentToolCall(name="web_search", params={"query": search_query}, reason="用户请求搜索信息")]
    elif wants_json:
        intent = "parse_json"
        confidence = 0.8
        plan = ["提取 JSON 文本", "调用 json_parse", "解释结果"]
        tool_calls = [AgentToolCall(name="json_parse", params={"text": text}, reason="用户请求解析 JSON")]
    elif wants_command:
        intent = "run_command"
        confidence = 0.72
        plan = ["提取命令内容", "调用 run_command", "总结执行结果"]
        tool_calls = [AgentToolCall(name="run_command", params={"command": text}, reason="用户请求执行本地命令")]
    else:
        plan = ["直接调用模型", "生成回答"]

    return AgentBrainResult(language=language, intent=intent, plan=plan, tool_calls=tool_calls, confidence=confidence)


def build_light_plan_exec(text: str) -> dict[str, Any]:
    brain = build_plan(text)
    plan_steps = [
        {
            "step": idx + 1,
            "title": item,
            "status": "pending",
        }
        for idx, item in enumerate(brain.plan)
    ]
    return {
        "mode": "react+plan-exec",
        "language": brain.language,
        "intent": brain.intent,
        "confidence": brain.confidence,
        "plan": brain.plan,
        "plan_exec": plan_steps,
        "tool_calls": [asdict(item) for item in brain.tool_calls],
    }


def react_next_decision(
    message: str,
    plan_exec: dict[str, Any],
    executed_steps: list[dict[str, Any]],
    force_tool: str | None = None,
    tool_params: dict[str, Any] | None = None,
    max_steps: int = 4,
) -> ReActDecision:
    tool_params = tool_params or {}
    step_index = len(executed_steps)

    if step_index >= max_steps:
        return ReActDecision(
            thought="已经达到最大行动步数，停止工具调用并交给模型总结。",
            action=None,
            should_stop=True,
            stop_reason="max_steps_reached",
        )

    if force_tool and step_index == 0:
        return ReActDecision(
            thought=f"用户显式指定工具 {force_tool}，优先执行该动作。",
            action=AgentToolCall(name=force_tool, params={k: str(v) for k, v in tool_params.items()}, reason="force_tool"),
        )

    planned_calls = plan_exec.get("tool_calls") or []
    if step_index < len(planned_calls):
        raw = planned_calls[step_index]
        call = AgentToolCall(
            name=str(raw.get("name", "")),
            params={k: str(v) for k, v in (raw.get("params") or {}).items()},
            reason=str(raw.get("reason", "")),
        )
        thought = (
            f"根据轻量计划执行第 {step_index + 1} 个动作：{call.name}。"
            f"目标是推进意图 {plan_exec.get('intent', 'chat')}。"
        )
        return ReActDecision(thought=thought, action=call)

    return ReActDecision(
        thought="计划中的工具动作已执行完成，停止行动并由模型生成最终答复。",
        action=None,
        should_stop=True,
        stop_reason="plan_completed",
    )


