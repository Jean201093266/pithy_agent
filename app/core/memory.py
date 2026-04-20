from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.core.db import AppDB


@dataclass
class MemoryConfig:
    short_window_messages: int = 12
    summary_trigger_messages: int = 20
    retrieval_top_k: int = 6
    long_term_cap: int = 400


class MemoryManager:
    """Coordinates short-term and long-term memory for a chat session."""

    def __init__(self, db: AppDB, config: MemoryConfig | None = None, llm_client: Any = None) -> None:
        self.db = db
        self.config = config or MemoryConfig()
        self._llm_client = llm_client  # Optional: for real embeddings

    def retrieve_context(self, message: str, session_id: str = "default") -> dict[str, Any]:
        short_term = self._build_short_term_context(session_id=session_id)
        query_embedding = self._embed_text(message)
        long_term = self.db.find_similar_memories(
            query_embedding=query_embedding,
            session_id=session_id,
            top_k=self.config.retrieval_top_k,
        )
        self.db.touch_memory_items([int(item["id"]) for item in long_term])

        hints: list[str] = []
        summary = short_term.get("summary") or ""
        if summary:
            hints.append(f"会话滚动摘要: {summary}")

        state = short_term.get("state") or {}
        if state:
            hints.append(f"结构化状态: {state}")

        if long_term:
            facts = [f"- ({item['memory_type']}) {item['text']}" for item in long_term]
            hints.append("长期记忆召回:\n" + "\n".join(facts))

        memory_prompt = "\n\n".join(hints).strip()
        return {
            "short_term": short_term,
            "long_term": long_term,
            "memory_prompt": memory_prompt,
            "context_messages": self._compose_context_messages(short_term, long_term),
        }

    def update_after_turn(
        self,
        user_message: str,
        assistant_reply: str,
        session_id: str = "default",
        tool_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        tool_trace = tool_trace or []
        added = 0

        # Episodic memory: persist each completed turn in compact form.
        episode = {
            "user": user_message[:1200],
            "assistant": assistant_reply[:1200],
            "tools": [step.get("tool", "") for step in tool_trace if step.get("tool")],
        }
        episode_text = f"用户: {episode['user']}\n助手: {episode['assistant']}"
        self.db.add_memory_item(
            memory_type="episodic",
            text=episode_text[:1800],
            payload=episode,
            importance=0.35,
            embedding=self._embed_text(episode_text),
            session_id=session_id,
        )
        added += 1

        for fact in self._extract_fact_memories(user_message):
            self.db.add_memory_item(
                memory_type=fact["memory_type"],
                text=fact["text"],
                payload=fact,
                importance=float(fact.get("importance", 0.6)),
                embedding=self._embed_text(fact["text"]),
                session_id=session_id,
            )
            added += 1

        self._update_structured_state(user_message=user_message, assistant_reply=assistant_reply, session_id=session_id)
        self._refresh_summary_if_needed(session_id=session_id)
        self._prune_long_term_memory(session_id=session_id)

        return {
            "added_memory_items": added,
            "summary": self.db.get_conversation_summary(session_id=session_id),
            "state": self.db.get_conversation_state(session_id=session_id),
        }

    def _build_short_term_context(self, session_id: str) -> dict[str, Any]:
        recent_messages = self.db.list_messages(limit=self.config.short_window_messages, session_id=session_id)
        return {
            "messages": recent_messages,
            "summary": self.db.get_conversation_summary(session_id=session_id),
            "state": self.db.get_conversation_state(session_id=session_id),
        }

    def _compose_context_messages(self, short_term: dict[str, Any], long_term: list[dict[str, Any]]) -> list[dict[str, str]]:
        context: list[dict[str, str]] = []
        summary = short_term.get("summary") or ""
        if summary:
            context.append({"role": "system", "content": f"Session summary: {summary}"})

        state = short_term.get("state") or {}
        if state:
            context.append({"role": "system", "content": f"Session state: {state}"})

        if long_term:
            bullet_points = "\n".join(f"- ({item['memory_type']}) {item['text']}" for item in long_term)
            context.append({"role": "system", "content": f"Retrieved long-term memories:\n{bullet_points}"})

        recent = short_term.get("messages") or []
        context.extend({"role": m["role"], "content": m["content"]} for m in recent if "role" in m and "content" in m)
        return context

    def _refresh_summary_if_needed(self, session_id: str) -> None:
        messages = self.db.list_messages(limit=self.config.summary_trigger_messages, session_id=session_id)
        if len(messages) < self.config.summary_trigger_messages:
            return

        key_lines: list[str] = []
        for msg in messages[-10:]:
            role = "U" if msg.get("role") == "user" else "A"
            text = str(msg.get("content", "")).replace("\n", " ").strip()
            if text:
                key_lines.append(f"{role}: {text[:140]}")

        if not key_lines:
            return

        summary = " | ".join(key_lines)[:1200]
        self.db.save_conversation_summary(summary, session_id=session_id)

    def _update_structured_state(self, user_message: str, assistant_reply: str, session_id: str) -> None:
        state = self.db.get_conversation_state(session_id=session_id)
        goals = state.get("goals") if isinstance(state.get("goals"), list) else []
        pending_steps = state.get("pending_steps") if isinstance(state.get("pending_steps"), list) else []

        lower = user_message.lower()
        if any(token in user_message for token in ["目标", "goal", "我要", "希望"]) and len(user_message) <= 120:
            goals.append(user_message)
        if any(token in lower for token in ["next", "然后", "接下来", "步骤"]) and len(user_message) <= 120:
            pending_steps.append(user_message)

        state["goals"] = goals[-8:]
        state["pending_steps"] = pending_steps[-8:]
        state["last_user_message"] = user_message[:500]
        state["last_assistant_reply"] = assistant_reply[:500]
        self.db.save_conversation_state(state, session_id=session_id)

    def _prune_long_term_memory(self, session_id: str) -> None:
        items = self.db.list_memory_items(session_id=session_id, limit=2000)
        if len(items) <= self.config.long_term_cap:
            return

        # Importance + access based forgetting curve.
        ranked = sorted(
            items,
            key=lambda item: (float(item.get("importance", 0.0)) * 0.75 + math.log1p(int(item.get("access_count", 0))) * 0.25),
            reverse=True,
        )
        keep_ids = {int(item["id"]) for item in ranked[: self.config.long_term_cap]}
        for item in items:
            if int(item["id"]) not in keep_ids:
                self.db.delete_memory_item(int(item["id"]))

    def _extract_fact_memories(self, text: str) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        cleaned = text.strip()
        if not cleaned:
            return facts

        pref_patterns = [
            r"(?:我喜欢|我偏好|请优先|请默认|prefer|always use)\s*(.+)",
            r"(?:我的习惯是|我的偏好是)\s*(.+)",
        ]
        for pat in pref_patterns:
            match = re.search(pat, cleaned, flags=re.IGNORECASE)
            if match:
                pref = match.group(1).strip("。.!? ")[:300]
                if pref:
                    facts.append({"memory_type": "preference", "text": pref, "importance": 0.8})
                break

        fact_patterns = [
            r"(?:我的|my)\s*([\w\u4e00-\u9fff]{1,20})\s*(?:是|is)\s*(.+)",
            r"(项目路径|project path|workspace)\s*(?:是|is)\s*(.+)",
        ]
        for pat in fact_patterns:
            match = re.search(pat, cleaned, flags=re.IGNORECASE)
            if match:
                fact_text = f"{match.group(1)}: {match.group(2).strip()[:300]}"
                facts.append({"memory_type": "fact", "text": fact_text, "importance": 0.7})
                break

        return facts

    def _embed_text(self, text: str, dims: int = 64) -> list[float]:
        # Use LLMClient.embed() if available (sentence-transformers or API)
        if self._llm_client is not None:
            try:
                from app.core.config_store import ModelConfig
                cfg = ModelConfig()  # minimal config for local embedding
                return self._llm_client.embed(text, cfg)
            except Exception:
                pass
        # Fallback: hash-based pseudo-embedding
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        vec = [0.0] * dims
        if not tokens:
            return vec
        for token in tokens:
            h = hash(token)
            idx = h % dims
            sign = 1.0 if (h >> 1) & 1 else -1.0
            vec[idx] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0.0:
            return vec
        return [v / norm for v in vec]

