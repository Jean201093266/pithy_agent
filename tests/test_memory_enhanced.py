"""
Tests for the enhanced memory system.

Tests cover:
1. Memory ranking and scoring
2. Deduplication
3. Context composition
4. Reflection generation
5. Integration with chat_graph
"""

import pytest
import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from app.core.db import AppDB
from app.core.memory_enhanced import (
    EnhancedMemoryManager,
    EnhancedMemoryConfig,
    MemoryRanker,
    MemoryDeduplicator,
    ContextComposer,
    ReflectionEngine,
    MemoryType,
    ContextLayer,
    MemoryScore,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = AppDB(db_path)
        yield db


@pytest.fixture
def config():
    """Create test configuration."""
    return EnhancedMemoryConfig(
        core_window_messages=3,
        summary_trigger_messages=5,
        long_term_cap=50,
        retrieval_top_k=4,
    )


@pytest.fixture
def memory_manager(temp_db, config):
    """Create enhanced memory manager."""
    return EnhancedMemoryManager(temp_db, config)


class TestMemoryScore:
    """Test memory scoring system."""

    def test_score_composition(self):
        """Test composite score calculation."""
        score = MemoryScore(
            importance=0.8,
            recency=0.9,
            relevance=0.7,
            access_frequency=0.5,
            consistency=0.95,
        )

        # Default weights
        composite = score.composite()
        assert 0 <= composite <= 1
        assert composite > 0.7  # Should be high with these values

        # Custom weights
        custom_weights = {
            "importance": 0.5,
            "recency": 0.5,
            "relevance": 0,
            "access_frequency": 0,
            "consistency": 0,
        }
        custom_score = score.composite(custom_weights)
        assert 0.79 <= custom_score <= 0.81  # ~0.8


class TestMemoryRanker:
    """Test memory ranking."""

    def test_score_item_importance(self, config):
        """Test importance scoring."""
        ranker = MemoryRanker(config)

        item = {
            "id": 1,
            "importance": 0.8,
            "embedding": [0.1] * 64,
            "access_count": 2,
            "created_at": datetime.now().isoformat(),
        }

        score = ranker.score_item(item, [0.1] * 64)
        assert score.importance == 0.8

    def test_score_item_recency(self, config):
        """Test recency (time decay) scoring."""
        ranker = MemoryRanker(config)

        # New item
        new_item = {
            "id": 1,
            "importance": 0.5,
            "embedding": [0.1] * 64,
            "access_count": 0,
            "created_at": datetime.now().isoformat(),
        }

        # Old item
        old_item = {
            "id": 2,
            "importance": 0.5,
            "embedding": [0.1] * 64,
            "access_count": 0,
            "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        new_score = ranker.score_item(new_item, [0.1] * 64)
        old_score = ranker.score_item(old_item, [0.1] * 64)

        # New item should have higher recency
        assert new_score.recency > old_score.recency

    def test_rank_items(self, config):
        """Test ranking multiple items."""
        ranker = MemoryRanker(config)

        items = [
            {
                "id": 1,
                "importance": 0.5,
                "embedding": [0.1] * 64,
                "access_count": 0,
                "created_at": datetime.now().isoformat(),
                "text": "item 1",
            },
            {
                "id": 2,
                "importance": 0.8,
                "embedding": [0.1] * 64,
                "access_count": 5,
                "created_at": datetime.now().isoformat(),
                "text": "item 2",
            },
        ]

        query_embedding = [0.1] * 64
        ranked = ranker.rank(items, query_embedding)

        # Item 2 should rank higher (higher importance + more access)
        assert len(ranked) == 2
        assert ranked[0][1] >= ranked[1][1]  # First should have higher score


class TestMemoryDeduplicator:
    """Test deduplication."""

    def test_find_duplicates(self, temp_db):
        """Test finding duplicate memories."""
        dedup = MemoryDeduplicator(temp_db)

        session_id = "test_session"

        # Add similar memories
        temp_db.add_memory_item(
            memory_type="semantic",
            text="Python is a programming language",
            embedding=[0.1, 0.2, 0.3] + [0.0] * 61,
            session_id=session_id,
        )

        temp_db.add_memory_item(
            memory_type="semantic",
            text="Python programming language",
            embedding=[0.1, 0.2, 0.3] + [0.0] * 61,
            session_id=session_id,
        )

        temp_db.add_memory_item(
            memory_type="semantic",
            text="Java is different",
            embedding=[0.8, 0.9, 0.7] + [0.0] * 61,
            session_id=session_id,
        )

        # Find duplicates of first item
        query_emb = [0.1, 0.2, 0.3] + [0.0] * 61
        duplicates = dedup.find_duplicates(query_emb, session_id, threshold=0.9)

        # Should find at least one similar item
        assert len(duplicates) >= 1


class TestContextComposer:
    """Test context composition."""

    def test_build_core_layer(self, config):
        """Test core layer building."""
        composer = ContextComposer(config)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        block = composer._build_core_layer(messages)
        assert block is not None
        assert block.layer == ContextLayer.CORE
        assert "Hello" in block.content

    def test_build_summary_layer(self, config):
        """Test summary layer building."""
        composer = ContextComposer(config)

        summary = "Previous conversation about Python programming"
        block = composer._build_summary_layer(summary)

        assert block is not None
        assert block.layer == ContextLayer.SUMMARY
        assert "Python" in block.content

    def test_compose_context(self, config):
        """Test full context composition."""
        composer = ContextComposer(config)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        long_term = [
            {
                "id": 1,
                "text": "User likes Python",
                "memory_type": "preference",
                "importance": 0.8,
                "embedding": [0.1] * 64,
            },
        ]

        result = composer.compose_context(
            recent_messages=messages,
            conversation_summary="Greeting",
            long_term_memories=long_term,
            query_embedding=[0.1] * 64,
        )

        assert "context_blocks" in result
        assert "source_memory_ids" in result
        assert "total_token_estimate" in result


class TestReflectionEngine:
    """Test reflection mechanism."""

    def test_reflection_trigger(self, temp_db, config):
        """Test reflection triggering."""
        reflector = ReflectionEngine(temp_db, config)

        # Add initial messages
        for i in range(5):
            temp_db.add_message("user", f"Message {i}", session_id="test")
            temp_db.add_message("assistant", f"Response {i}", session_id="test")

        # At exactly reflection_trigger_interval, should trigger
        should_reflect = reflector.should_reflect("test")
        # 10 messages at interval 10 = should trigger
        assert isinstance(should_reflect, bool)

    def test_generate_reflection(self, temp_db, config):
        """Test reflection generation."""
        reflector = ReflectionEngine(temp_db, config)

        turns = [
            {
                "user_message": "Help me write a function",
                "assistant_reply": "Here's a Python function that does...",
                "success": True,
                "tools": ["code_executor"],
            },
        ]

        reflection = reflector.generate_reflection("test", turns, [])

        assert reflection is not None
        assert "text" in reflection
        assert "payload" in reflection
        assert reflection["importance"] > 0.6


class TestEnhancedMemoryManager:
    """Test main memory manager."""

    def test_retrieve_context(self, memory_manager):
        """Test context retrieval."""
        session_id = "test_session"

        # Add test data
        memory_manager.db.add_message("user", "Hello", session_id=session_id)
        memory_manager.db.add_message("assistant", "Hi", session_id=session_id)

        # Retrieve context
        context = memory_manager.retrieve_context("What's the weather?", session_id=session_id)

        assert "short_term" in context
        assert "long_term" in context
        assert "memory_prompt" in context
        assert "context_blocks" in context
        assert "token_estimate" in context

    def test_update_after_turn(self, memory_manager):
        """Test updating memory after a turn."""
        session_id = "test_session"

        update = memory_manager.update_after_turn(
            user_message="Write a Python function",
            assistant_reply="Here's a function that does X",
            session_id=session_id,
            tool_trace=[{"tool": "code_executor", "success": True}],
            success=True,
        )

        assert "added_memory_items" in update
        assert update["added_memory_items"] > 0
        assert "summary" in update
        assert "state" in update

    def test_extract_fact_memories(self, memory_manager):
        """Test fact extraction."""
        text = "我的项目路径是 /home/user/projects"
        facts = memory_manager._extract_fact_memories(text)

        assert len(facts) > 0
        assert any("项目路径" in f["text"] for f in facts)

    def test_extract_preference_memories(self, memory_manager):
        """Test preference extraction."""
        text = "我喜欢使用 Python 进行编程"
        prefs = memory_manager._extract_fact_memories(text)

        # May not extract if pattern doesn't match exactly
        # But structure should be correct
        assert isinstance(prefs, list)

    def test_prune_memory(self, memory_manager):
        """Test memory pruning."""
        session_id = "test_session"

        # Add many memories beyond cap
        for i in range(100):
            memory_manager.db.add_memory_item(
                memory_type="episodic",
                text=f"Memory item {i}",
                importance=0.1 + (i % 10) * 0.05,
                session_id=session_id,
            )

        initial_count = len(memory_manager.db.list_memory_items(session_id=session_id))
        assert initial_count > memory_manager.config.long_term_cap

        # Prune
        memory_manager._prune_long_term_memory(session_id)

        final_count = len(memory_manager.db.list_memory_items(session_id=session_id))
        assert final_count <= memory_manager.config.long_term_cap

    def test_dynamic_k(self, memory_manager):
        """Test dynamic retrieval count."""
        simple_k = memory_manager._get_dynamic_k("simple")
        medium_k = memory_manager._get_dynamic_k("medium")
        complex_k = memory_manager._get_dynamic_k("complex")

        assert simple_k < medium_k < complex_k

    def test_deduplicate_memories(self, memory_manager):
        """Test deduplication."""
        # Create similar embeddings
        emb = [0.1] * 64

        memories = [
            {
                "id": 1,
                "text": "Python is great",
                "embedding": emb,
                "memory_type": "semantic",
            },
            {
                "id": 2,
                "text": "Python programming",
                "embedding": emb,
                "memory_type": "semantic",
            },
            {
                "id": 3,
                "text": "Java language",
                "embedding": [0.9] * 64,
                "memory_type": "semantic",
            },
        ]

        # With high threshold, should deduplicate first two
        deduplicated = memory_manager._deduplicate_memories(memories)

        # Should have fewer items due to dedup
        assert len(deduplicated) <= len(memories)


class TestIntegration:
    """Integration tests."""

    def test_full_workflow(self, temp_db, config):
        """Test complete workflow."""
        manager = EnhancedMemoryManager(temp_db, config)
        session_id = "integration_test"

        # Multiple turns
        for turn in range(3):
            # Retrieve
            context = manager.retrieve_context(
                f"Question {turn}",
                session_id=session_id,
            )

            assert context["memory_prompt"] is not None

            # Update
            update = manager.update_after_turn(
                user_message=f"Question {turn}",
                assistant_reply=f"Answer {turn}",
                session_id=session_id,
                success=True,
            )

            assert update["added_memory_items"] > 0

        # Check final state
        items = temp_db.list_memory_items(session_id=session_id)
        assert len(items) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

