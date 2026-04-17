#!/usr/bin/env python3
"""
快速启动脚本 - 一键本地运行所有验证和示例
"""

import subprocess
import sys
import os
from pathlib import Path


def run_command(cmd, description):
    """运行命令并显示进度"""
    print(f"\n{'='*60}")
    print(f"▶️  {description}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=False)
        if result.returncode == 0:
            print(f"✅ {description} 完成\n")
            return True
        else:
            print(f"❌ {description} 失败 (exit code: {result.returncode})\n")
            return False
    except Exception as e:
        print(f"❌ 错误: {e}\n")
        return False


def main():
    """主程序"""
    print("\n" + "="*60)
    print("🚀 增强记忆系统 - 本地运行快速启动")
    print("="*60)

    # 检查项目结构
    print("\n📁 检查项目结构...")
    required_files = [
        "app/core/memory_enhanced.py",
        "app/core/chat_graph.py",
        "docs/ENHANCED_MEMORY_GUIDE.md",
        "tests/test_memory_enhanced.py",
        "verify_enhanced_memory.py",
    ]

    missing = []
    for f in required_files:
        if not Path(f).exists():
            missing.append(f)

    if missing:
        print(f"❌ 缺少文件:")
        for f in missing:
            print(f"  - {f}")
        return False

    print("✅ 所有必需文件都存在\n")

    # 1. 验证安装
    success = run_command(
        "python verify_enhanced_memory.py",
        "1️⃣  验证系统安装"
    )
    if not success:
        print("⚠️  验证失败，继续执行...")

    # 2. 编译检查
    success = run_command(
        "python -m py_compile app/core/memory_enhanced.py app/core/chat_graph.py",
        "2️⃣  检查代码语法"
    )
    if not success:
        return False

    # 3. 运行测试
    success = run_command(
        "pytest tests/test_memory_enhanced.py -v --tb=short",
        "3️⃣  运行单元测试"
    )
    if not success:
        print("⚠️  某些测试失败，继续...")

    # 4. 运行示例
    success = run_command(
        "python app/core/examples_enhanced_memory.py",
        "4️⃣  运行示例代码"
    )
    if not success:
        print("⚠️  示例运行出错...")

    # 完成
    print("\n" + "="*60)
    print("✅ 本地运行验证完成!")
    print("="*60)
    print("\n📚 后续步骤:")
    print("  1. 查看文档: cat docs/QUICK_REFERENCE.md")
    print("  2. 启动服务: python run.py")
    print("  3. 自己写代码: 参考 LOCAL_SETUP_GUIDE.md")
    print("\n" + "="*60 + "\n")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

