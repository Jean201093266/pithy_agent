# 增强记忆系统 - 本地运行完全指南

**最后更新**：2026-04-17  
**版本**：v1.0.0

---

## 📋 目录

1. [系统要求](#系统要求)
2. [环境准备](#环境准备)
3. [依赖安装](#依赖安装)
4. [数据库初始化](#数据库初始化)
5. [启动服务](#启动服务)
6. [验证安装](#验证安装)
7. [运行示例](#运行示例)
8. [运行测试](#运行测试)
9. [常见问题](#常见问题)

---

## 系统要求

### 最低配置
- **OS**：Windows / macOS / Linux
- **Python**：3.9+ (推荐 3.10+)
- **内存**：2GB+
- **磁盘**：100MB+
- **端口**：8000 (可配置)

### 推荐配置
- **OS**：Windows 10+、macOS 12+、Ubuntu 20.04+
- **Python**：3.11+
- **内存**：4GB+
- **磁盘**：500MB+
- **网络**：稳定连接（可选，用于 LLM API）

---

## 环境准备

### Step 1: 检查 Python 版本

**Windows**：
```powershell
python --version
# 输出：Python 3.11.x 或更高
```

**macOS/Linux**：
```bash
python3 --version
# 输出：Python 3.11.x 或更高
```

### Step 2: 创建虚拟环境

**Windows**：
```powershell
# 进入项目目录
cd D:\projects\github\pithy_agent

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
.venv\Scripts\Activate.ps1
```

**macOS/Linux**：
```bash
# 进入项目目录
cd ~/projects/github/pithy_agent

# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate
```

✅ **验证**：命令行前缀出现 `(.venv)`

### Step 3: 升级 pip 和工具

```bash
# 升级 pip
python -m pip install --upgrade pip

# 升级 setuptools 和 wheel
pip install --upgrade setuptools wheel
```

---

## 依赖安装

### 安装必要包

```bash
# 进入项目目录
cd pithy_agent

# 安装所有依赖（使用 requirements.txt）
pip install -r requirements.txt
```

### 检查关键依赖

```bash
# 检查是否安装成功
python -c "import sqlite3; print('✓ sqlite3 OK')"
python -c "import langgraph; print('✓ langgraph OK')"
python -c "from langchain import *; print('✓ langchain OK')"
```

✅ **预期输出**：
```
✓ sqlite3 OK
✓ langgraph OK
✓ langchain OK
```

---

## 数据库初始化

### 创建数据目录

```bash
# Windows
mkdir data

# macOS/Linux
mkdir -p data
```

### 初始化数据库

```bash
# Python 脚本自动初始化，首次运行时自动创建
python -c "
from app.core.db import AppDB
from pathlib import Path
db = AppDB(Path('data/agent.db'))
print('✅ Database initialized at data/agent.db')
"
```

✅ **验证**：应该看到 `data/agent.db` 文件被创建

---

## 启动服务

### 方式 1：直接运行 Python 服务器

```bash
# 确保虚拟环境已激活 (.venv)
python run.py
```

✅ **预期输出**：
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

### 方式 2：使用 Uvicorn 直接启动

```bash
# 安装 uvicorn (if not already)
pip install uvicorn

# 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 方式 3：在生产环境运行

```bash
# 使用 Gunicorn (推荐生产)
pip install gunicorn

# 启动
gunicorn app.main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### 停止服务

```bash
# Windows: Ctrl + C
# macOS/Linux: Ctrl + C
```

---

## 验证安装

### 方式 1：使用验证脚本（推荐）

```bash
# 运行官方验证脚本
python verify_enhanced_memory.py
```

✅ **预期输出**：
```
============================================================
Enhanced Memory System - Verification
============================================================

1. Core Modules
✓ memory_enhanced
✓ chat_graph_enhanced

2. Chat Graph Enhancement
✓ ChatGraphEngineWithEnhancedMemory found
✓ create_chat_graph_engine factory found

3. Documentation Files
✓ User Guide
✓ Implementation Checklist
✓ Implementation Summary
✓ Quick Reference

4. Code Examples
✓ Usage Examples

5. Test Suite
✓ Unit Tests

6. Runtime Verification
✓ Core classes importable
✓ Config instantiation (cap=600)
✓ MemoryType enum complete
✓ ContextLayer enum complete

============================================================
✓ All checks passed! System is ready to use.
============================================================
```

### 方式 2：API 健康检查

```bash
# 在另一个终端运行（服务仍在运行）
curl http://127.0.0.1:8000/api/health

# 或使用 Python requests
python -c "
import requests
response = requests.get('http://127.0.0.1:8000/api/health')
print(f'Status: {response.status_code}')
print(f'Response: {response.json()}')
"
```

✅ **预期输出**：
```json
{"status": "ok"}
```

### 方式 3：Python 导入测试

```bash
python -c "
from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig
from app.core.chat_graph import create_chat_graph_engine

config = EnhancedMemoryConfig()
print(f'✓ Config: long_term_cap={config.long_term_cap}')
print(f'✓ Config: reflection_enabled={config.reflection_enabled}')
print('✅ All imports successful!')
"
```

---

## 运行示例

### 示例 1：快速验证（3 分钟）

```bash
# 运行快速示例
python app/core/examples_enhanced_memory.py
```

✅ **输出内容**：7 个示例的运行结果

### 示例 2：自己写代码

创建文件 `test_local.py`：

```python
#!/usr/bin/env python3
"""本地测试脚本"""

from pathlib import Path
from app.core.db import AppDB
from app.core.memory_enhanced import EnhancedMemoryManager, EnhancedMemoryConfig

# 初始化数据库
db = AppDB(Path("data/agent.db"))

# 创建配置
config = EnhancedMemoryConfig(
    core_window_messages=6,
    long_term_cap=100,
    reflection_trigger_interval=3,
)

# 创建记忆管理器
manager = EnhancedMemoryManager(db, config)

# 测试基本操作
session_id = "test_session"

# 1. 检索上下文
print("1️⃣  检索上下文...")
context = manager.retrieve_context(
    message="你好，我是测试用户",
    session_id=session_id,
)
print(f"   Token 估计: {context['token_estimate']}")

# 2. 更新记忆
print("2️⃣  更新记忆...")
update = manager.update_after_turn(
    user_message="我喜欢使用 Python",
    assistant_reply="很好，Python 是一门强大的语言",
    session_id=session_id,
    success=True,
)
print(f"   新增记忆: {update['added_memory_items']} 条")

# 3. 查看统计
print("3️⃣  内存统计...")
items = db.list_memory_items(session_id=session_id)
print(f"   总记忆数: {len(items)}")

print("\n✅ 本地测试完成！")
```

运行测试：

```bash
python test_local.py
```

✅ **预期输出**：
```
1️⃣  检索上下文...
   Token 估计: 150
2️⃣  更新记忆...
   新增记忆: 2 条
3️⃣  内存统计...
   总记忆数: 2

✅ 本地测试完成！
```

---

## 运行测试

### 方式 1：运行所有测试

```bash
# 安装 pytest（如果未安装）
pip install pytest pytest-cov

# 运行所有测试
pytest tests/test_memory_enhanced.py -v
```

✅ **预期输出**：
```
tests/test_memory_enhanced.py::TestMemoryScore::test_score_composition PASSED
tests/test_memory_enhanced.py::TestMemoryRanker::test_score_item_importance PASSED
tests/test_memory_enhanced.py::TestMemoryRanker::test_score_item_recency PASSED
...
======================== 20+ passed in 2.34s ========================
```

### 方式 2：运行特定测试

```bash
# 只运行 Memory Ranker 的测试
pytest tests/test_memory_enhanced.py::TestMemoryRanker -v

# 只运行 Reflection Engine 的测试
pytest tests/test_memory_enhanced.py::TestReflectionEngine -v
```

### 方式 3：生成覆盖率报告

```bash
# 运行测试并生成覆盖率报告
pytest tests/test_memory_enhanced.py --cov=app.core.memory_enhanced --cov-report=html

# 在浏览器中查看
# Windows: start htmlcov/index.html
# macOS: open htmlcov/index.html
# Linux: firefox htmlcov/index.html
```

---

## 常见问题

### ❌ 问题 1：Python 版本不兼容

**错误**：
```
ModuleNotFoundError: No module named 'app'
```

**解决**：
```bash
# 检查 Python 版本
python --version

# 应该是 3.9+ (推荐 3.11+)
# 如果不是，请升级 Python

# 确保在项目根目录
cd pithy_agent

# 确保虚拟环境已激活
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
```

### ❌ 问题 2：依赖安装失败

**错误**：
```
ERROR: Could not find a version that satisfies the requirement...
```

**解决**：
```bash
# 升级 pip
python -m pip install --upgrade pip

# 清除缓存后重新安装
pip install --no-cache-dir -r requirements.txt

# 或逐个安装
pip install langgraph langchain sqlite3
```

### ❌ 问题 3：数据库锁定

**错误**：
```
sqlite3.OperationalError: database is locked
```

**解决**：
```bash
# 停止所有 Python 进程
# Windows: taskkill /IM python.exe /F
# macOS/Linux: pkill -f python

# 删除锁定文件（如果存在）
rm data/agent.db-wal
rm data/agent.db-shm

# 重新启动服务
python run.py
```

### ❌ 问题 4：端口 8000 已被占用

**错误**：
```
Address already in use
```

**解决**：
```bash
# 方式 1：杀死占用端口的进程
# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# macOS/Linux
lsof -i :8000
kill -9 <PID>

# 方式 2：使用不同的端口
python run.py --port 8001
```

### ❌ 问题 5：导入模块失败

**错误**：
```
ModuleNotFoundError: No module named 'langchain'
```

**解决**：
```bash
# 确保虚拟环境已激活
which python  # macOS/Linux
where python  # Windows

# 应该显示 .venv 目录中的 python

# 重新安装依赖
pip install -r requirements.txt --force-reinstall
```

### ✅ 问题 6：验证脚本显示某些检查失败

**现象**：
```
❌ Some checks failed
```

**解决**：
```bash
# 检查文件是否存在
ls -la docs/ENHANCED_MEMORY_GUIDE.md

# 检查代码是否编译
python -m py_compile app/core/memory_enhanced.py

# 查看详细错误日志
python verify_enhanced_memory.py 2>&1 | tee verify.log

# 查看 verify.log 文件找出具体问题
cat verify.log
```

---

## 工作流程

### 完整本地开发流程

```bash
# 1. 激活虚拟环境
# Windows
.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate

# 2. 验证安装
python verify_enhanced_memory.py

# 3. 运行测试
pytest tests/test_memory_enhanced.py -v

# 4. 查看示例
python app/core/examples_enhanced_memory.py

# 5. 启动服务
python run.py

# 6. 在另一个终端测试 API
curl http://127.0.0.1:8000/api/health
```

### 开发迭代流程

```bash
# 1. 修改代码
# 编辑 app/core/memory_enhanced.py

# 2. 快速检查语法
python -m py_compile app/core/memory_enhanced.py

# 3. 运行相关测试
pytest tests/test_memory_enhanced.py::TestMemoryRanker -v

# 4. 测试改动
python test_local.py

# 5. 重新启动服务
# Ctrl + C 停止
# python run.py 重启
```

---

## 性能监控

### 查看日志

```bash
# 查看应用日志
tail -f logs/agent.log  # macOS/Linux
type logs\agent.log     # Windows

# 查看实时日志（需要运行中的服务）
# 在服务运行终端可以看到实时日志
```

### 内存使用

```bash
# 监控 Python 进程内存
# Windows
Get-Process python | Select Name,WorkingSet

# macOS/Linux
ps aux | grep python

# 或使用专门工具
pip install psutil
python -c "
import psutil
p = psutil.Process()
print(f'Memory: {p.memory_info().rss / 1024 / 1024:.1f} MB')
"
```

### 数据库大小

```bash
# 检查数据库文件大小
# Windows
dir data\agent.db

# macOS/Linux
ls -lh data/agent.db

# 输出示例：
# -rw-r--r-- 1 user staff 512K Apr 17 10:00 data/agent.db
```

---

## 下一步

✅ **已完成本地环境搭建，现在可以：**

1. **运行示例**
   ```bash
   python app/core/examples_enhanced_memory.py
   ```

2. **运行测试**
   ```bash
   pytest tests/test_memory_enhanced.py -v
   ```

3. **启动服务**
   ```bash
   python run.py
   ```

4. **自己开发**
   - 修改代码
   - 运行测试
   - 验证改动

5. **查看文档**
   - `docs/ENHANCED_MEMORY_GUIDE.md` - 详细指南
   - `docs/QUICK_REFERENCE.md` - 快速参考

---

## 📞 获取帮助

- 遇到问题？ → 查看"常见问题"部分
- 需要示例？ → 运行 `app/core/examples_enhanced_memory.py`
- 想了解更多？ → 查看 `docs/ENHANCED_MEMORY_GUIDE.md`
- 验证安装？ → 运行 `verify_enhanced_memory.py`

---

**祝你运行愉快！** 🚀

*最后更新：2026-04-17*  
*版本：v1.0.0*

