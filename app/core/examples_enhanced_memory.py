"""
Example usage of the enhanced memory system.

This file demonstrates how to integrate enhanced memory into the chat application.
"""

from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ============================================================================
# Example 1: Basic Integration
# ============================================================================

def example_basic_integration():
    """
    Basic example of enabling enhanced memory.

    This is the recommended approach for most use cases.
    """
    from app.core.db import AppDB
    from app.core.memory import MemoryManager
    from app.core.langchain_adapter import LangChainAdapter
    from app.core.chat_graph import create_chat_graph_engine

    # Initialize database
    db_path = Path("data/agent.db")
    db = AppDB(db_path)

    # Initialize LLM adapter
    adapter = LangChainAdapter()

    # Create basic memory manager (still needed for compatibility)
    memory_manager = MemoryManager(db)

    # Create chat engine WITH enhanced memory (just one flag!)
    engine = create_chat_graph_engine(
        adapter=adapter,
        memory_manager=memory_manager,
        use_enhanced_memory=True,  # Enable enhanced mode
    )

    # Now use engine as usual, but with advanced memory features
    if engine.available:
        print("✓ Enhanced memory engine initialized")

        result = engine.run(
            message="Write a Python function to calculate fibonacci",
            cfg=None,
            session_id="example_session",
            enabled_tools=[],
            is_mock=True,
        )

        print(f"✓ Conversation completed")
        print(f"  Memory items added: {result.get('memory_update', {}).get('added_memory_items', 0)}")
        print(f"  Reflection generated: {'reflection' in result.get('memory_update', {})}")


# ============================================================================
# Example 2: Custom Configuration
# ============================================================================

def example_custom_configuration():
    """
    Advanced example with custom configuration for different user tiers.
    """
    from app.core.db import AppDB
    from app.core.memory import MemoryManager
    from app.core.memory_enhanced import EnhancedMemoryConfig, EnhancedMemoryManager
    from app.core.langchain_adapter import LangChainAdapter
    from app.core.chat_graph import ChatGraphEngineWithEnhancedMemory

    db_path = Path("data/agent.db")
    db = AppDB(db_path)
    adapter = LangChainAdapter()
    memory_manager = MemoryManager(db)

    # Different configs for different user tiers
    configs = {
        "free": EnhancedMemoryConfig(
            core_window_messages=4,
            long_term_cap=200,
            retrieval_top_k=3,
            reflection_enabled=False,
        ),
        "pro": EnhancedMemoryConfig(
            core_window_messages=6,
            long_term_cap=600,
            retrieval_top_k=6,
            reflection_enabled=True,
            reflection_trigger_interval=5,
        ),
        "enterprise": EnhancedMemoryConfig(
            core_window_messages=12,
            long_term_cap=2000,
            retrieval_top_k=12,
            reflection_enabled=True,
            reflection_trigger_interval=3,
        ),
    }

    # Create engine for a pro user
    user_tier = "pro"
    config = configs[user_tier]

    engine = ChatGraphEngineWithEnhancedMemory(
        adapter=adapter,
        memory_manager=memory_manager,
        use_enhanced=True,
    )

    # Inject custom config
    engine.enhanced_memory.config = config

    print(f"✓ Enhanced engine configured for {user_tier} tier")
    print(f"  Long-term memory cap: {config.long_term_cap}")
    print(f"  Reflection enabled: {config.reflection_enabled}")


# ============================================================================
# Example 3: Memory Inspection and Management
# ============================================================================

def example_memory_inspection():
    """
    Example of inspecting and managing memory after conversations.
    """
    from app.core.db import AppDB
    from app.core.memory_enhanced import EnhancedMemoryManager

    db_path = Path("data/agent.db")
    db = AppDB(db_path)
    manager = EnhancedMemoryManager(db)

    session_id = "example_session"

    # Get memory statistics
    items = db.list_memory_items(session_id=session_id)

    print(f"Session: {session_id}")
    print(f"Total memories: {len(items)}")

    # Group by type
    by_type = {}
    by_importance = {"high": 0, "medium": 0, "low": 0}

    for item in items:
        memory_type = item["memory_type"]
        by_type[memory_type] = by_type.get(memory_type, 0) + 1

        importance = item["importance"]
        if importance >= 0.7:
            by_importance["high"] += 1
        elif importance >= 0.4:
            by_importance["medium"] += 1
        else:
            by_importance["low"] += 1

    print(f"\nBy type:")
    for mtype, count in sorted(by_type.items()):
        print(f"  {mtype}: {count}")

    print(f"\nBy importance:")
    for level, count in by_importance.items():
        print(f"  {level}: {count}")

    # Show highest importance memories
    print(f"\nTop 5 important memories:")
    top_items = sorted(items, key=lambda x: x["importance"], reverse=True)[:5]
    for item in top_items:
        print(f"  [{item['memory_type']}] {item['text'][:60]}... (importance={item['importance']:.2f})")


# ============================================================================
# Example 4: Multi-Session Management
# ============================================================================

def example_multi_session():
    """
    Example of managing multiple concurrent sessions with different memory strategies.
    """
    from app.core.db import AppDB
    from app.core.memory import MemoryManager
    from app.core.langchain_adapter import LangChainAdapter
    from app.core.chat_graph import create_chat_graph_engine

    db_path = Path("data/agent.db")
    db = AppDB(db_path)
    adapter = LangChainAdapter()
    memory_manager = MemoryManager(db)

    # Create single engine (can handle multiple sessions internally)
    engine = create_chat_graph_engine(
        adapter=adapter,
        memory_manager=memory_manager,
        use_enhanced_memory=True,
    )

    # Simulate multiple user sessions
    sessions = [
        {"id": "user_alice", "messages": ["What's AI?", "How does it work?"]},
        {"id": "user_bob", "messages": ["Help with Python", "Show code examples"]},
    ]

    for session in sessions:
        session_id = session["id"]
        print(f"\nProcessing session: {session_id}")

        for i, message in enumerate(session["messages"]):
            print(f"  Turn {i+1}: {message}")

            # Each session maintains its own memory context
            # The engine automatically uses session_id for isolation

            # In a real app, you would call:
            # result = engine.run(
            #     message=message,
            #     cfg=config,
            #     session_id=session_id,
            #     enabled_tools=tools,
            #     is_mock=False,
            # )

    print("\n✓ Multi-session management complete")


# ============================================================================
# Example 5: Reflection-Driven Learning
# ============================================================================

def example_reflection_learning():
    """
    Example of how the agent learns from reflections.
    """
    from app.core.db import AppDB
    from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig

    db_path = Path("data/agent.db")
    db = AppDB(db_path)

    # Configure frequent reflection
    config = EnhancedMemoryConfig(
        reflection_trigger_interval=3,  # Every 3 turns
    )

    manager = EnhancedMemoryManager(db, config)
    session_id = "learning_session"

    print("Starting learning session with reflections...\n")

    # Simulate 10 conversation turns
    for turn in range(10):
        user_msg = f"Task number {turn + 1}"
        assistant_msg = f"Solution for task {turn + 1}"
        tool_trace = [{"tool": "executor", "success": True}]

        # Update memory (which may trigger reflection)
        update = manager.update_after_turn(
            user_message=user_msg,
            assistant_reply=assistant_msg,
            session_id=session_id,
            tool_trace=tool_trace,
            success=True,
        )

        if turn % 3 == 2:  # Show every 3 turns
            reflection = update.get("reflection")
            if reflection:
                print(f"Turn {turn + 1}: REFLECTION GENERATED")
                print(f"  Text: {reflection['text'][:100]}...")
            else:
                print(f"Turn {turn + 1}: Regular memory update")

    # Check reflection memories
    items = db.list_memory_items(session_id=session_id)
    reflections = [i for i in items if i["memory_type"] == "reflection"]

    print(f"\n✓ Learning complete")
    print(f"  Total memories: {len(items)}")
    print(f"  Reflections learned: {len(reflections)}")


# ============================================================================
# Example 6: Context Complexity-Based Retrieval
# ============================================================================

def example_complexity_based_retrieval():
    """
    Example of how retrieval adapts to query complexity.
    """
    from app.core.db import AppDB
    from app.core.memory_enhanced import EnhancedMemoryManager

    db_path = Path("data/agent.db")
    db = AppDB(db_path)
    manager = EnhancedMemoryManager(db)

    session_id = "complexity_test"

    # Add some test memories
    for i in range(20):
        db.add_memory_item(
            memory_type="semantic",
            text=f"Knowledge item {i}",
            importance=0.5 + (i % 10) * 0.05,
            session_id=session_id,
        )

    # Different query complexities
    queries = [
        ("What?", "simple"),                          # Simple query
        ("How to implement X?", "medium"),            # Medium complexity
        ("Design a system for X with considerations Y and Z", "complex"),  # Complex
    ]

    print("Testing query complexity-based retrieval:\n")

    for query, expected_complexity in queries:
        context = manager.retrieve_context(query, session_id=session_id)
        actual_complexity = manager._estimate_complexity(query)

        memory_count = len(context.get("long_term", []))
        token_estimate = context.get("token_estimate", 0)

        print(f"Query: {query}")
        print(f"  Expected: {expected_complexity}")
        print(f"  Detected: {actual_complexity}")
        print(f"  Memories retrieved: {memory_count}")
        print(f"  Token estimate: {token_estimate}")
        print()


# ============================================================================
# Example 7: Token Budget Management
# ============================================================================

def example_token_management():
    """
    Example of managing token budgets to prevent context overflow.
    """
    from app.core.db import AppDB
    from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig

    db_path = Path("data/agent.db")
    db = AppDB(db_path)

    # Strict token budgets
    config = EnhancedMemoryConfig(
        short_term_budget=800,      # Only 800 tokens for recent messages
        long_term_budget=1200,      # Only 1200 tokens for memories
    )

    manager = EnhancedMemoryManager(db, config)

    session_id = "token_management"

    # Add many long messages
    for i in range(50):
        db.add_message(
            role="user",
            content="This is a very long message. " * 50,
            session_id=session_id,
        )

    # Retrieve with strict budgets
    context = manager.retrieve_context(
        "What was discussed?",
        session_id=session_id,
    )

    token_estimate = context.get("token_estimate", 0)

    print(f"Token budget management:")
    print(f"  Short-term budget: {config.short_term_budget}")
    print(f"  Long-term budget: {config.long_term_budget}")
    print(f"  Total budget: {config.short_term_budget + config.long_term_budget}")
    print(f"  Actual usage: {token_estimate}")
    print(f"  Status: {'✓ Within budget' if token_estimate <= config.short_term_budget + config.long_term_budget else '✗ Over budget'}")


# ============================================================================
# Main: Run all examples
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("Enhanced Memory System Examples")
    print("=" * 70)

    try:
        print("\n[Example 1] Basic Integration")
        print("-" * 70)
        example_basic_integration()
    except Exception as e:
        print(f"Error in example 1: {e}")

    try:
        print("\n[Example 2] Custom Configuration")
        print("-" * 70)
        example_custom_configuration()
    except Exception as e:
        print(f"Error in example 2: {e}")

    try:
        print("\n[Example 3] Memory Inspection")
        print("-" * 70)
        example_memory_inspection()
    except Exception as e:
        print(f"Error in example 3: {e}")

    try:
        print("\n[Example 4] Multi-Session Management")
        print("-" * 70)
        example_multi_session()
    except Exception as e:
        print(f"Error in example 4: {e}")

    try:
        print("\n[Example 5] Reflection-Driven Learning")
        print("-" * 70)
        example_reflection_learning()
    except Exception as e:
        print(f"Error in example 5: {e}")

    try:
        print("\n[Example 6] Complexity-Based Retrieval")
        print("-" * 70)
        example_complexity_based_retrieval()
    except Exception as e:
        print(f"Error in example 6: {e}")

    try:
        print("\n[Example 7] Token Budget Management")
        print("-" * 70)
        example_token_management()
    except Exception as e:
        print(f"Error in example 7: {e}")

    print("\n" + "=" * 70)
    print("All examples completed!")
    print("=" * 70)

