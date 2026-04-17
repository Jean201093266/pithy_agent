"""
Enhanced memory system with advanced retrieval, ranking, and reflection.

Features:
1. Hierarchical context (core/summary/retrieval layers)
2. Smart memory ranking and forgetting curve
3. Memory deduplication and cleaning
4. Reflection mechanism for learning
5. Dynamic retrieval strategies
6. Importance scoring with multiple signals
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any, Optional
from enum import Enum

from app.core.db import AppDB

LOGGER = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Classification of memory types."""
    EPISODIC = "episodic"        # 情景记忆：交互记录
    SEMANTIC = "semantic"        # 语义记忆：知识规则
    PREFERENCE = "preference"    # 偏好记忆：用户偏好
    FACT = "fact"               # 事实记忆：确定事实
    REFLECTION = "reflection"   # 反思记忆：经验教训
    ERROR = "error"             # 错误记忆：失败记录


class ContextLayer(str, Enum):
    """Hierarchy of context layers."""
    CORE = "core"           # 最近 2-3 轮核心对话
    SUMMARY = "summary"     # 历史摘要
    RETRIEVAL = "retrieval" # 从长期记忆召回


@dataclass
class MemoryScore:
    """Composite importance score for a memory item."""
    importance: float      # Base importance [0, 1]
    recency: float        # Time decay factor
    relevance: float      # Semantic relevance to query
    access_frequency: float  # Normalized access count
    consistency: float    # Deduplication score (high if unique)

    def composite(self, weights: dict[str, float] | None = None) -> float:
        """Compute weighted composite score."""
        w = weights or {
            "importance": 0.35,
            "recency": 0.25,
            "relevance": 0.25,
            "access_frequency": 0.10,
            "consistency": 0.05,
        }
        return (
            self.importance * w.get("importance", 0.35) +
            self.recency * w.get("recency", 0.25) +
            self.relevance * w.get("relevance", 0.25) +
            self.access_frequency * w.get("access_frequency", 0.10) +
            self.consistency * w.get("consistency", 0.05)
        )


@dataclass
class ContextBlock:
    """Single block of context for the LLM."""
    layer: ContextLayer
    content: str
    token_estimate: int
    source_ids: list[int]


@dataclass
class EnhancedMemoryConfig:
    """Configuration for enhanced memory system."""
    # Short-term context
    core_window_messages: int = 6      # 核心对话轮数
    summary_trigger_messages: int = 20  # 触发摘要的阈值

    # Long-term memory
    long_term_cap: int = 600           # 长期记忆容量
    dedup_similarity_threshold: float = 0.85  # 去重相似度阈值

    # Retrieval
    retrieval_top_k: int = 8           # 基础检索数量
    dynamic_k_range: tuple[int, int] = (2, 8)  # 动态检索范围
    rerank_enabled: bool = True        # 启用重排
    rerank_top_k: int = 3              # 重排后保留数量

    # Reflection
    reflection_trigger_interval: int = 10  # 每 N 轮触发反思
    reflection_enabled: bool = True

    # Forgetting curve
    access_decay_halflife: int = 7     # 7 天半衰期
    importance_threshold: float = 0.15  # 删除低于此重要度的记忆

    # Token budgets
    short_term_budget: int = 2000       # 短期记忆 token 预算
    long_term_budget: int = 3000        # 长期记忆 token 预算


class MemoryDeduplicator:
    """Handles deduplication of similar memories."""

    def __init__(self, db: AppDB):
        self.db = db

    def find_duplicates(
        self,
        query_embedding: list[float],
        session_id: str,
        threshold: float = 0.85,
        limit: int = 100,
    ) -> list[tuple[int, float]]:
        """Find duplicate/similar memories above threshold."""
        items = self.db.list_memory_items(session_id=session_id, limit=limit)
        duplicates: list[tuple[int, float]] = []

        for item in items:
            emb = item.get("embedding", [])
            if not emb:
                continue
            sim = self._cosine_similarity(query_embedding, emb)
            if sim >= threshold:
                duplicates.append((item["id"], sim))

        return sorted(duplicates, key=lambda x: x[1], reverse=True)

    def merge_duplicates(
        self,
        primary_id: int,
        duplicate_ids: list[int],
        session_id: str,
    ) -> None:
        """Merge duplicate memories into primary, keeping only the best."""
        primary = self.db.list_memory_items(session_id=session_id, limit=1)
        if not primary:
            return

        # Delete duplicates, keep primary
        for dup_id in duplicate_ids:
            if dup_id != primary_id:
                self.db.delete_memory_item(dup_id)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity."""
        size = min(len(a), len(b))
        if size == 0:
            return 0.0
        dot = sum(float(a[i]) * float(b[i]) for i in range(size))
        norm_a = math.sqrt(sum(float(a[i]) * float(a[i]) for i in range(size)))
        norm_b = math.sqrt(sum(float(b[i]) * float(b[i]) for i in range(size)))
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


class MemoryRanker:
    """Ranks memories by composite score."""

    def __init__(self, config: EnhancedMemoryConfig):
        self.config = config

    def score_item(
        self,
        item: dict[str, Any],
        query_embedding: list[float],
        now: datetime = None,
        total_access_count: int = 1,
    ) -> MemoryScore:
        """Compute composite score for a memory item."""
        now = now or datetime.now()

        # Importance: base score
        importance = float(item.get("importance", 0.5))

        # Recency: time decay
        created_str = item.get("created_at", "")
        if created_str:
            try:
                created = datetime.fromisoformat(created_str)
                age_days = (now - created).days
                halflife = self.config.access_decay_halflife
                recency = 2.0 ** (-age_days / halflife) if halflife > 0 else 1.0
            except (ValueError, TypeError):
                recency = 0.5
        else:
            recency = 0.5

        # Relevance: semantic similarity
        emb = item.get("embedding", [])
        if emb and query_embedding:
            relevance = self._cosine_similarity(query_embedding, emb)
        else:
            relevance = 0.0

        # Access frequency: normalized
        access_count = int(item.get("access_count", 0))
        access_frequency = min(1.0, access_count / max(1, total_access_count))

        # Consistency: assume high for now (dedup would lower this)
        consistency = 0.9

        return MemoryScore(
            importance=importance,
            recency=recency,
            relevance=relevance,
            access_frequency=access_frequency,
            consistency=consistency,
        )

    def rank(
        self,
        items: list[dict[str, Any]],
        query_embedding: list[float],
        weights: dict[str, float] | None = None,
    ) -> list[tuple[dict[str, Any], float]]:
        """Rank items by composite score."""
        total_access = sum(item.get("access_count", 0) for item in items)
        scored = []

        for item in items:
            score = self.score_item(item, query_embedding, total_access_count=max(1, total_access))
            composite = score.composite(weights)
            scored.append((item, composite))

        return sorted(scored, key=lambda x: x[1], reverse=True)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity."""
        size = min(len(a), len(b))
        if size == 0:
            return 0.0
        dot = sum(float(a[i]) * float(b[i]) for i in range(size))
        norm_a = math.sqrt(sum(float(a[i]) * float(a[i]) for i in range(size)))
        norm_b = math.sqrt(sum(float(b[i]) * float(b[i]) for i in range(size)))
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


class ContextComposer:
    """Composes hierarchical context for LLM."""

    def __init__(self, config: EnhancedMemoryConfig):
        self.config = config
        self.ranker = MemoryRanker(config)

    def compose_context(
        self,
        recent_messages: list[dict[str, Any]],
        conversation_summary: str,
        long_term_memories: list[dict[str, Any]],
        query_embedding: list[float],
    ) -> dict[str, Any]:
        """Compose hierarchical context with three layers."""
        blocks: list[ContextBlock] = []

        # Layer 1: Core - recent messages (必需)
        core_block = self._build_core_layer(recent_messages)
        if core_block:
            blocks.append(core_block)

        # Layer 2: Summary - compressed history (可选)
        summary_block = self._build_summary_layer(conversation_summary)
        if summary_block:
            blocks.append(summary_block)

        # Layer 3: Retrieval - ranked long-term memories (可选)
        if long_term_memories:
            retrieval_block = self._build_retrieval_layer(
                long_term_memories,
                query_embedding,
            )
            if retrieval_block:
                blocks.append(retrieval_block)

        # Compose final context respecting token budgets
        return self._compose_blocks(blocks)

    def _build_core_layer(self, messages: list[dict[str, Any]]) -> Optional[ContextBlock]:
        """Build core layer from recent messages."""
        if not messages:
            return None

        lines = []
        total_tokens = 0

        for msg in messages[-self.config.core_window_messages:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            line = f"{role.capitalize()}: {content}"
            token_est = len(line.split()) + 2

            if total_tokens + token_est <= self.config.short_term_budget:
                lines.append(line)
                total_tokens += token_est

        if not lines:
            return None

        content = "\n".join(lines)
        return ContextBlock(
            layer=ContextLayer.CORE,
            content=content,
            token_estimate=total_tokens,
            source_ids=[],
        )

    def _build_summary_layer(self, summary: str) -> Optional[ContextBlock]:
        """Build summary layer from conversation summary."""
        if not summary or not summary.strip():
            return None

        content = f"会话摘要: {summary}"
        token_est = len(content.split()) + 5

        if token_est > self.config.short_term_budget // 2:
            content = content[:400]
            token_est = len(content.split()) + 5

        return ContextBlock(
            layer=ContextLayer.SUMMARY,
            content=content,
            token_estimate=token_est,
            source_ids=[],
        )

    def _build_retrieval_layer(
        self,
        memories: list[dict[str, Any]],
        query_embedding: list[float],
    ) -> Optional[ContextBlock]:
        """Build retrieval layer from long-term memories."""
        if not memories:
            return None

        # Rank by composite score
        ranked = self.ranker.rank(memories, query_embedding)

        lines = []
        total_tokens = 0

        for item, score in ranked:
            if total_tokens >= self.config.long_term_budget:
                break

            memory_type = item.get("memory_type", "unknown")
            text = item.get("text", "")[:200]
            line = f"[{memory_type}] {text}"
            token_est = len(line.split()) + 2

            if total_tokens + token_est <= self.config.long_term_budget:
                lines.append(line)
                total_tokens += token_est

        if not lines:
            return None

        content = "长期记忆:\n" + "\n".join(lines)
        return ContextBlock(
            layer=ContextLayer.RETRIEVAL,
            content=content,
            token_estimate=total_tokens,
            source_ids=[int(item["id"]) for item, _ in ranked[:len(lines)]],
        )

    def _compose_blocks(self, blocks: list[ContextBlock]) -> dict[str, Any]:
        """Compose blocks into final context."""
        system_messages = []
        all_source_ids = []

        for block in blocks:
            if block.layer == ContextLayer.CORE:
                system_messages.append(("system_core", block.content))
            elif block.layer == ContextLayer.SUMMARY:
                system_messages.append(("system_summary", block.content))
            elif block.layer == ContextLayer.RETRIEVAL:
                system_messages.append(("system_retrieval", block.content))
            all_source_ids.extend(block.source_ids)

        return {
            "context_blocks": system_messages,
            "source_memory_ids": all_source_ids,
            "total_token_estimate": sum(b.token_estimate for b in blocks),
        }


class ReflectionEngine:
    """Generates reflections on agent performance."""

    def __init__(self, db: AppDB, config: EnhancedMemoryConfig):
        self.db = db
        self.config = config

    def should_reflect(self, session_id: str) -> bool:
        """Check if reflection should be triggered."""
        if not self.config.reflection_enabled:
            return False

        messages = self.db.list_messages(limit=1000, session_id=session_id)
        return len(messages) % self.config.reflection_trigger_interval == 0

    def generate_reflection(
        self,
        session_id: str,
        recent_turns: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]] | None = None,
    ) -> Optional[dict[str, Any]]:
        """Generate reflection memory from recent interactions."""
        if not recent_turns:
            return None

        tool_trace = tool_trace or []

        # Analyze recent turns
        successes = []
        failures = []
        patterns = []

        for turn in recent_turns[-5:]:
            user_msg = turn.get("user_message", "")
            assistant_reply = turn.get("assistant_reply", "")

            # Simple heuristics
            if any(w in assistant_reply.lower() for w in ["error", "fail", "unable", "无法"]):
                failures.append(user_msg[:100])
            else:
                successes.append(user_msg[:100])

            # Extract patterns from tools used
            for tool in tool_trace:
                if tool.get("success"):
                    patterns.append(f"工具 {tool.get('tool')} 有效处理: {user_msg[:80]}")

        # Compose reflection
        reflection_text = "Agent 反思记录:\n"
        if successes:
            reflection_text += f"✓ 成功案例: {', '.join(successes[:2])}\n"
        if failures:
            reflection_text += f"✗ 失败案例及改进: {', '.join(failures[:2])}\n"
        if patterns:
            reflection_text += f"✓ 有效模式: {patterns[0]}\n"

        reflection_payload = {
            "successes": successes,
            "failures": failures,
            "patterns": patterns,
            "turn_count": len(recent_turns),
        }

        return {
            "text": reflection_text.strip(),
            "payload": reflection_payload,
            "importance": 0.65,
        }


class EnhancedMemoryManager:
    """Main enhanced memory management system."""

    def __init__(self, db: AppDB, config: EnhancedMemoryConfig | None = None):
        self.db = db
        self.config = config or EnhancedMemoryConfig()
        self.deduplicator = MemoryDeduplicator(db)
        self.ranker = MemoryRanker(self.config)
        self.composer = ContextComposer(self.config)
        self.reflector = ReflectionEngine(db, self.config)
        self._recent_turns: dict[str, list[dict[str, Any]]] = {}

    def retrieve_context(
        self,
        message: str,
        session_id: str = "default",
        complexity: str = "medium",
    ) -> dict[str, Any]:
        """
        Retrieve hierarchical context for LLM reasoning.

        complexity: "simple", "medium", "complex"
        - simple: top 2 memories
        - medium: top 4 memories
        - complex: top 6-8 memories
        """
        # Get recent messages (core layer)
        recent_messages = self.db.list_messages(
            limit=self.config.core_window_messages,
            session_id=session_id,
        )

        # Get conversation summary (summary layer)
        conversation_summary = self.db.get_conversation_summary(session_id=session_id)

        # Dynamic retrieval based on complexity
        k = self._get_dynamic_k(complexity)
        query_embedding = self._embed_text(message)

        # Retrieve long-term memories with dedup check
        all_memories = self.db.find_similar_memories(
            query_embedding=query_embedding,
            session_id=session_id,
            top_k=k * 2,  # Fetch more to account for dedup
        )

        # Deduplicate
        deduplicated = self._deduplicate_memories(all_memories)

        # Compose hierarchical context
        context_data = self.composer.compose_context(
            recent_messages=recent_messages,
            conversation_summary=conversation_summary,
            long_term_memories=deduplicated[:k],
            query_embedding=query_embedding,
        )

        # Build final prompt
        memory_prompt = self._build_memory_prompt(context_data)

        # Track access for scoring
        self.db.touch_memory_items(context_data.get("source_memory_ids", []))

        return {
            "short_term": recent_messages,
            "long_term": deduplicated[:k],
            "memory_prompt": memory_prompt,
            "context_blocks": context_data.get("context_blocks", []),
            "token_estimate": context_data.get("total_token_estimate", 0),
        }

    def update_after_turn(
        self,
        user_message: str,
        assistant_reply: str,
        session_id: str = "default",
        tool_trace: list[dict[str, Any]] | None = None,
        success: bool = True,
    ) -> dict[str, Any]:
        """Update memory after a complete turn."""
        tool_trace = tool_trace or []
        added = 0

        # Track for reflection
        if session_id not in self._recent_turns:
            self._recent_turns[session_id] = []
        self._recent_turns[session_id].append({
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "success": success,
            "tools": [t.get("tool") for t in tool_trace],
        })

        # Add episodic memory
        episode = {
            "user": user_message[:1000],
            "assistant": assistant_reply[:1000],
            "tools": [step.get("tool", "") for step in tool_trace],
            "success": success,
        }
        episode_text = f"用户: {episode['user']}\n助手: {episode['assistant']}"
        importance = 0.6 if success else 0.55

        self.db.add_memory_item(
            memory_type=MemoryType.EPISODIC.value,
            text=episode_text[:1800],
            payload=episode,
            importance=importance,
            embedding=self._embed_text(episode_text),
            session_id=session_id,
        )
        added += 1

        # Extract and add fact/preference memories
        for fact in self._extract_fact_memories(user_message):
            self.db.add_memory_item(
                memory_type=fact["memory_type"],
                text=fact["text"],
                payload=fact,
                importance=float(fact.get("importance", 0.65)),
                embedding=self._embed_text(fact["text"]),
                session_id=session_id,
            )
            added += 1

        # Update structured state
        self._update_structured_state(user_message, assistant_reply, session_id)

        # Refresh summary if needed
        self._refresh_summary_if_needed(session_id)

        # Generate reflection if needed
        reflection_memory = None
        if self.reflector.should_reflect(session_id):
            reflection = self.reflector.generate_reflection(
                session_id,
                self._recent_turns.get(session_id, []),
                tool_trace,
            )
            if reflection:
                self.db.add_memory_item(
                    memory_type=MemoryType.REFLECTION.value,
                    text=reflection["text"],
                    payload=reflection["payload"],
                    importance=reflection["importance"],
                    embedding=self._embed_text(reflection["text"]),
                    session_id=session_id,
                )
                added += 1
                reflection_memory = reflection

        # Prune and clean memories
        self._prune_long_term_memory(session_id)
        self._clean_stale_memories(session_id)

        return {
            "added_memory_items": added,
            "summary": self.db.get_conversation_summary(session_id=session_id),
            "state": self.db.get_conversation_state(session_id=session_id),
            "reflection": reflection_memory,
        }

    def _get_dynamic_k(self, complexity: str) -> int:
        """Get dynamic retrieval count based on query complexity."""
        min_k, max_k = self.config.dynamic_k_range
        if complexity == "simple":
            return min_k
        elif complexity == "complex":
            return max_k
        else:
            return (min_k + max_k) // 2

    def _deduplicate_memories(self, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove near-duplicate memories."""
        if not memories:
            return []

        # Simple clustering: keep first, remove similar
        kept = [memories[0]]
        for current in memories[1:]:
            is_duplicate = False
            for kept_item in kept:
                sim = self.deduplicator._cosine_similarity(
                    current.get("embedding", []),
                    kept_item.get("embedding", []),
                )
                if sim >= self.config.dedup_similarity_threshold:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(current)

        return kept

    def _build_memory_prompt(self, context_data: dict[str, Any]) -> str:
        """Build final memory prompt for LLM."""
        blocks = context_data.get("context_blocks", [])
        lines = []

        for block_type, content in blocks:
            if block_type == "system_core":
                lines.append(f"【最近对话】\n{content}")
            elif block_type == "system_summary":
                lines.append(f"\n【会话历史】\n{content}")
            elif block_type == "system_retrieval":
                lines.append(f"\n【相关记忆】\n{content}")

        return "\n".join(lines) if lines else ""

    def _extract_fact_memories(self, text: str) -> list[dict[str, Any]]:
        """Extract fact and preference memories from text."""
        facts: list[dict[str, Any]] = []
        cleaned = text.strip()
        if not cleaned:
            return facts

        # Preference patterns
        pref_patterns = [
            r"(?:我喜欢|我偏好|请优先|请默认|prefer|always use)\s*(.+)",
            r"(?:我的习惯是|我的偏好是)\s*(.+)",
        ]
        for pat in pref_patterns:
            match = re.search(pat, cleaned, flags=re.IGNORECASE)
            if match:
                pref = match.group(1).strip("。.!? ")[:300]
                if pref:
                    facts.append({
                        "memory_type": MemoryType.PREFERENCE.value,
                        "text": pref,
                        "importance": 0.75,
                    })
                break

        # Fact patterns
        fact_patterns = [
            r"(?:我的|my)\s*([\w\u4e00-\u9fff]{1,20})\s*(?:是|is)\s*(.+)",
            r"(项目路径|project path|workspace)\s*(?:是|is)\s*(.+)",
        ]
        for pat in fact_patterns:
            match = re.search(pat, cleaned, flags=re.IGNORECASE)
            if match:
                fact_text = f"{match.group(1)}: {match.group(2).strip()[:300]}"
                facts.append({
                    "memory_type": MemoryType.FACT.value,
                    "text": fact_text,
                    "importance": 0.70,
                })
                break

        return facts

    def _update_structured_state(
        self,
        user_message: str,
        assistant_reply: str,
        session_id: str,
    ) -> None:
        """Update structured conversation state."""
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
        state["updated_at"] = datetime.now().isoformat()

        self.db.save_conversation_state(state, session_id=session_id)

    def _refresh_summary_if_needed(self, session_id: str) -> None:
        """Refresh conversation summary if message count exceeds threshold."""
        messages = self.db.list_messages(limit=self.config.summary_trigger_messages, session_id=session_id)
        if len(messages) < self.config.summary_trigger_messages:
            return

        # Create summary from last 10 messages
        key_lines: list[str] = []
        for msg in messages[-10:]:
            role = "U" if msg.get("role") == "user" else "A"
            text = str(msg.get("content", "")).replace("\n", " ").strip()
            if text:
                key_lines.append(f"{role}: {text[:120]}")

        if not key_lines:
            return

        summary = " | ".join(key_lines)[:1500]
        self.db.save_conversation_summary(summary, session_id=session_id)

    def _prune_long_term_memory(self, session_id: str) -> None:
        """Prune long-term memory to cap."""
        items = self.db.list_memory_items(session_id=session_id, limit=2000)
        if len(items) <= self.config.long_term_cap:
            return

        # Rank by composite score and keep top N
        query_embedding = self._embed_text("")  # Neutral query
        ranked = self.ranker.rank(items, query_embedding)

        keep_ids = {int(item["id"]) for item, _ in ranked[:self.config.long_term_cap]}
        for item in items:
            if int(item["id"]) not in keep_ids:
                self.db.delete_memory_item(int(item["id"]))

        LOGGER.info(f"Pruned memory: {len(items)} -> {self.config.long_term_cap} items")

    def _clean_stale_memories(self, session_id: str) -> None:
        """Clean stale/low-importance memories."""
        items = self.db.list_memory_items(session_id=session_id, limit=2000)

        now = datetime.now()
        for item in items:
            importance = float(item.get("importance", 0.5))

            # Check if low importance and old
            created_str = item.get("created_at", "")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str)
                    age_days = (now - created).days

                    # Aggressive cleanup for old, low-importance items
                    if age_days > 30 and importance < self.config.importance_threshold:
                        self.db.delete_memory_item(item["id"])
                except (ValueError, TypeError):
                    pass

    @staticmethod
    def _embed_text(text: str, dims: int = 64) -> list[float]:
        """Generate simple embedding for text."""
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

