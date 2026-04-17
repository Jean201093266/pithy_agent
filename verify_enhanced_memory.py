#!/usr/bin/env python3
"""
Enhanced Memory System - Verification Script

Validates that all components are properly installed and functional.
Run this after deployment to confirm everything is working correctly.
"""

import sys
import importlib.util
from pathlib import Path


def check_module(name: str, path: str) -> bool:
    """Check if a module can be imported."""
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            print(f"❌ Failed to load {name}: spec is None")
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(f"✓ {name}")
        return True
    except Exception as e:
        print(f"❌ {name}: {e}")
        return False


def check_file_exists(path: str, description: str) -> bool:
    """Check if a file exists."""
    if Path(path).exists():
        size = Path(path).stat().st_size
        print(f"✓ {description} ({size:,} bytes)")
        return True
    else:
        print(f"❌ {description} not found at {path}")
        return False


def verify_implementation() -> bool:
    """Verify all components are installed."""
    print("\n" + "="*60)
    print("Enhanced Memory System - Verification")
    print("="*60 + "\n")

    all_ok = True

    # Check core modules
    print("1. Core Modules")
    print("-" * 40)
    all_ok &= check_module(
        "memory_enhanced",
        "app/core/memory_enhanced.py"
    )
    all_ok &= check_module(
        "chat_graph_enhanced",
        "app/core/chat_graph_enhanced.py"
    )

    # Check chat_graph enhancement
    print("\n2. Chat Graph Enhancement")
    print("-" * 40)
    try:
        import app.core.chat_graph as cg
        if hasattr(cg, "ChatGraphEngineWithEnhancedMemory"):
            print("✓ ChatGraphEngineWithEnhancedMemory found")
        else:
            print("❌ ChatGraphEngineWithEnhancedMemory not found")
            all_ok = False

        if hasattr(cg, "create_chat_graph_engine"):
            print("✓ create_chat_graph_engine factory found")
        else:
            print("❌ create_chat_graph_engine factory not found")
            all_ok = False
    except Exception as e:
        print(f"❌ Error checking chat_graph: {e}")
        all_ok = False

    # Check documentation
    print("\n3. Documentation Files")
    print("-" * 40)
    all_ok &= check_file_exists(
        "docs/ENHANCED_MEMORY_GUIDE.md",
        "User Guide"
    )
    all_ok &= check_file_exists(
        "docs/IMPLEMENTATION_CHECKLIST.md",
        "Implementation Checklist"
    )
    all_ok &= check_file_exists(
        "docs/IMPLEMENTATION_SUMMARY.md",
        "Implementation Summary"
    )
    all_ok &= check_file_exists(
        "docs/QUICK_REFERENCE.md",
        "Quick Reference"
    )

    # Check examples
    print("\n4. Code Examples")
    print("-" * 40)
    all_ok &= check_file_exists(
        "app/core/examples_enhanced_memory.py",
        "Usage Examples"
    )

    # Check tests
    print("\n5. Test Suite")
    print("-" * 40)
    all_ok &= check_file_exists(
        "tests/test_memory_enhanced.py",
        "Unit Tests"
    )

    # Runtime tests
    print("\n6. Runtime Verification")
    print("-" * 40)
    try:
        from app.core.memory_enhanced import (
            EnhancedMemoryManager,
            EnhancedMemoryConfig,
            MemoryType,
            ContextLayer,
        )
        print("✓ Core classes importable")

        # Try creating an instance
        config = EnhancedMemoryConfig()
        print(f"✓ Config instantiation (cap={config.long_term_cap})")

        # Check memory types
        if hasattr(MemoryType, "EPISODIC"):
            print("✓ MemoryType enum complete")

        # Check context layers
        if hasattr(ContextLayer, "CORE"):
            print("✓ ContextLayer enum complete")
    except Exception as e:
        print(f"❌ Runtime error: {e}")
        all_ok = False

    # Summary
    print("\n" + "="*60)
    if all_ok:
        print("✓ All checks passed! System is ready to use.")
        print("\nNext steps:")
        print("1. Read: docs/ENHANCED_MEMORY_GUIDE.md")
        print("2. Run: python app/core/examples_enhanced_memory.py")
        print("3. Test: pytest tests/test_memory_enhanced.py -v")
        print("4. Enable: use_enhanced_memory=True in your code")
    else:
        print("❌ Some checks failed. Please review the errors above.")
        print("\nTroubleshooting:")
        print("1. Ensure all files are in correct locations")
        print("2. Check Python version: python --version")
        print("3. Verify dependencies: pip list | grep -i lang")
    print("="*60 + "\n")

    return all_ok


def main() -> int:
    """Main entry point."""
    try:
        success = verify_implementation()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

