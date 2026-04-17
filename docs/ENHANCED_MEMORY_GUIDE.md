# 增强记忆系统迁移指南

## 概述

此文档介绍如何在 pithy_agent 中启用增强的记忆系统。增强系统提供：

### 核心特性

1. **分层上下文（Hierarchical Context）**
   - 核心层：最近 2-3 轮对话
   - 摘要层：历史对话压缩摘要
   - 检索层：从长期记忆召回的相关信息
   - 优势：Token 更省、信息更准

2. **智能重排（Ranking）**
   - 基础重要度（用户标记）
   - 时间衰减（遗忘曲线）
   - 语义相关性（向量相似度）
   - 访问频率（使用次数）
   - 去重因子（唯一性）
   - 综合评分：最相关的记忆优先

3. **记忆去重（Deduplication）**
   - 自动检测相似记忆
   - 聚类合并重复内容
   - 保留最完整版本

4. **反思机制（Reflection）**
   - 每 N 轮自动反思一次
   - 提取成功/失败经验
   - 识别有效工具使用模式
   - 生成学习记忆供后续使用

5. **动态召回策略（Dynamic Retrieval）**
   - 简单查询：检索 2-3 条记忆
   - 中等复杂度：检索 4-5 条记忆
   - 复杂任务：检索 6-8 条记忆
   - 自动估计查询复杂度

6. **遗忘机制（Forgetting Curve）**
   - 基于访问频率和时间的衰减
   - 低重要度旧记忆自动删除
   - 防止记忆库无限膨胀

---

## 快速开始

### 1. 使用工厂函数（推荐）

在 `main.py` 或应用初始化代码中：

```python
from app.core.chat_graph import create_chat_graph_engine
from app.core.memory import MemoryManager

# 创建基础内存管理器
memory_manager = MemoryManager(db)

# 使用工厂函数，自动启用增强模式
engine = create_chat_graph_engine(
    adapter=adapter,
    memory_manager=memory_manager,
    use_enhanced_memory=True,  # 启用增强模式
)
```

### 2. 直接使用增强版本

```python
from app.core.chat_graph import ChatGraphEngineWithEnhancedMemory
from app.core.memory import MemoryManager

memory_manager = MemoryManager(db)
engine = ChatGraphEngineWithEnhancedMemory(
    adapter=adapter,
    memory_manager=memory_manager,
    use_enhanced=True,
)
```

### 3. 自定义配置

```python
from app.core.chat_graph import ChatGraphEngineWithEnhancedMemory
from app.core.memory_enhanced import EnhancedMemoryConfig, EnhancedMemoryManager
from app.core.memory import MemoryManager

# 自定义增强记忆配置
custom_config = EnhancedMemoryConfig(
    core_window_messages=8,           # 核心对话轮数
    long_term_cap=800,                # 长期记忆容量
    retrieval_top_k=10,               # 基础检索数量
    reflection_trigger_interval=5,    # 每 5 轮反思一次
    access_decay_halflife=14,         # 14 天半衰期
)

# 创建增强内存管理器
enhanced_memory = EnhancedMemoryManager(db, custom_config)

# 更新 MemoryManager 的 db 引用（保持兼容性）
memory_manager = MemoryManager(db)

# 使用增强版本
engine = ChatGraphEngineWithEnhancedMemory(
    adapter=adapter,
    memory_manager=memory_manager,
    use_enhanced=True,
)
```

---

## 配置说明

### EnhancedMemoryConfig 参数

#### 短期记忆配置
```python
core_window_messages: int = 6        # 保持最近 N 轮核心对话
summary_trigger_messages: int = 20   # 每 N 条消息触发摘要
```

#### 长期记忆配置
```python
long_term_cap: int = 600                    # 长期记忆最大条数
dedup_similarity_threshold: float = 0.85    # 去重相似度阈值 (0-1)
```

#### 检索配置
```python
retrieval_top_k: int = 8                    # 基础检索数量
dynamic_k_range: tuple[int, int] = (2, 8)  # 动态检索范围 [最小, 最大]
rerank_enabled: bool = True                 # 启用重排
rerank_top_k: int = 3                       # 重排后保留数量
```

#### 反思配置
```python
reflection_trigger_interval: int = 10       # 每 N 轮触发反思
reflection_enabled: bool = True             # 启用反思机制
```

#### 遗忘曲线配置
```python
access_decay_halflife: int = 7              # 7 天半衰期
importance_threshold: float = 0.15          # 删除低于 0.15 的记忆
```

#### Token 预算配置
```python
short_term_budget: int = 2000               # 短期记忆 token 预算
long_term_budget: int = 3000                # 长期记忆 token 预算
```

---

## 核心API

### EnhancedMemoryManager

#### 主要方法

```python
manager = EnhancedMemoryManager(db, config)

# 检索分层上下文
context = manager.retrieve_context(
    message="用户查询",
    session_id="session_123",
    complexity="medium",  # "simple", "medium", "complex"
)

# 返回结构：
# {
#     "short_term": [...],            # 最近消息
#     "long_term": [...],             # 检索到的记忆
#     "memory_prompt": "...",         # 格式化的记忆提示词
#     "context_blocks": [...],        # 分层上下文块
#     "token_estimate": 2500,         # 估计 token 数
# }

# 更新内存（每轮对话后调用）
update = manager.update_after_turn(
    user_message="用户消息",
    assistant_reply="助手回复",
    session_id="session_123",
    tool_trace=[...],      # 工具调用记录
    success=True,          # 是否成功
)

# 返回结构：
# {
#     "added_memory_items": 3,        # 新增记忆条数
#     "summary": "...",               # 对话摘要
#     "state": {...},                 # 结构化状态
#     "reflection": {...},            # 反思记忆（如果生成）
# }
```

### 内存类型

```python
from app.core.memory_enhanced import MemoryType

MemoryType.EPISODIC    # 交互记录
MemoryType.SEMANTIC    # 知识规则
MemoryType.PREFERENCE  # 用户偏好
MemoryType.FACT        # 确定事实
MemoryType.REFLECTION  # 经验教训
MemoryType.ERROR       # 失败记录
```

### 复杂度估计

```python
from app.core.chat_graph_enhanced import ChatGraphWithEnhancedMemory

# 系统自动估计：
# - 简单（simple）：< 50 字，单个问号
# - 复杂（complex）：包含多步骤关键词 + > 15 词
# - 中等（medium）：其他

# 手动指定：
context = manager.retrieve_context(
    message="...",
    session_id="...",
    complexity="complex",  # 强制使用复杂策略
)
```

---

## 数据库集成

增强系统使用现有的 `memory_items` 表：

```sql
-- 已有列（支持增强系统）
id              -- 记忆 ID
session_id      -- 会话 ID
memory_type     -- 记忆类型 (episodic/semantic/...)
text            -- 记忆文本
payload_json    -- 元数据 JSON
importance      -- 重要度 [0, 1]
embedding_json  -- 向量嵌入
access_count    -- 访问次数（用于评分）
created_at      -- 创建时间（用于时间衰减）
updated_at      -- 更新时间
```

### 所需索引

```python
# 已创建
CREATE INDEX idx_memory_items_session ON memory_items(session_id, created_at DESC);
```

---

## 性能优化建议

### 1. Token 预算管理

```python
# 设置合理的 Token 预算避免上下文溢出
config = EnhancedMemoryConfig(
    short_term_budget=1500,   # 短期记忆 ~1500 token
    long_term_budget=2000,    # 长期记忆 ~2000 token
)
```

### 2. 记忆容量管理

```python
# 保持长期记忆在合理范围内
config = EnhancedMemoryConfig(
    long_term_cap=500,  # 不超过 500 条记忆
)
```

### 3. 定期清理

```python
# 系统自动清理，但可以手动触发
manager._clean_stale_memories(session_id="session_123")
manager._prune_long_term_memory(session_id="session_123")
```

### 4. 检索优化

```python
# 为常见查询使用简单模式
context = manager.retrieve_context(
    message="简单问题",
    session_id="...",
    complexity="simple",  # 只检索 2 条记忆
)
```

---

## 监测和调试

### 日志配置

```python
import logging

# 启用详细日志
logger = logging.getLogger("app.core.memory_enhanced")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
```

### 查看记忆统计

```python
from app.core.db import AppDB

db = AppDB(...)
items = db.list_memory_items(session_id="session_123")

print(f"总记忆数: {len(items)}")
by_type = {}
for item in items:
    memory_type = item["memory_type"]
    by_type[memory_type] = by_type.get(memory_type, 0) + 1

print(f"按类型分布: {by_type}")
print(f"平均重要度: {sum(i['importance'] for i in items) / len(items)}")
```

### 调试记忆检索

```python
# 启用调试模式查看检索过程
context = manager.retrieve_context(
    message="查询文本",
    session_id="session_123",
    complexity="complex",
)

print(f"检索到 {len(context['long_term'])} 条记忆")
print(f"Token 估计: {context['token_estimate']}")
for mem in context['long_term']:
    print(f"  - [{mem['memory_type']}] {mem['text'][:50]}... (importance={mem['importance']})")
```

---

## 迁移路径

### 阶段 1：共存（向后兼容）
- 保留原 MemoryManager
- 新增 EnhancedMemoryManager
- 两套系统可并行运行

### 阶段 2：试运行
```python
# 在部分会话启用增强模式
if is_premium_user:
    use_enhanced_memory = True
else:
    use_enhanced_memory = False
```

### 阶段 3：完全迁移
```python
# 全量切换到增强系统
engine = create_chat_graph_engine(
    adapter=adapter,
    memory_manager=memory_manager,
    use_enhanced_memory=True,
)
```

---

## 故障排除

### 问题 1：记忆检索为空

```python
# 原因：新会话还没有记忆
# 解决：正常，使用核心层对话即可

# 原因：记忆被认为不相关
# 解决：调整去重阈值或重要度阈值
config = EnhancedMemoryConfig(
    dedup_similarity_threshold=0.75,  # 降低去重门槛
    importance_threshold=0.1,         # 降低删除门槛
)
```

### 问题 2：Token 超出预算

```python
# 原因：记忆太多或太长
# 解决：
config = EnhancedMemoryConfig(
    short_term_budget=1000,    # 减少预算
    long_term_budget=1500,
    retrieval_top_k=4,         # 减少检索数量
)
```

### 问题 3：反思不生成

```python
# 原因：还未达到触发间隔
# 解决：继续对话直到达到 reflection_trigger_interval

# 原因：反思内容不足
# 解决：增加对话轮数或减少 reflection_trigger_interval
config = EnhancedMemoryConfig(
    reflection_trigger_interval=5,  # 每 5 轮反思
)
```

---

## 最佳实践

### ✅ 推荐做法

1. **使用工厂函数** - 自动处理兼容性

```python
from app.core.chat_graph import create_chat_graph_engine

engine = create_chat_graph_engine(adapter, memory_manager, use_enhanced_memory=True)
```

2. **根据用户层级定制配置**

```python
if user.is_vip:
    config = EnhancedMemoryConfig(
        long_term_cap=1000,
        reflection_trigger_interval=3,
    )
else:
    config = EnhancedMemoryConfig()
```

3. **定期监测内存大小**

```python
# 在定时任务中
items = db.list_memory_items(session_id=session_id)
if len(items) > 800:
    manager._clean_stale_memories(session_id)
```

### ❌ 避免做法

1. **不要禁用所有过滤** - 会导致记忆库爆炸

```python
# 不推荐
config = EnhancedMemoryConfig(
    importance_threshold=0,
    dedup_similarity_threshold=0,
)
```

2. **不要混用两个系统的检索结果** - 可能重复

3. **不要频繁改变配置** - 会影响评分稳定性

---

## 下一步优化

### 已实现
- ✅ 分层上下文
- ✅ 智能重排
- ✅ 去重机制
- ✅ 反思机制
- ✅ 遗忘曲线

### 未来规划
- ⏳ 多跳检索（multi-hop retrieval）
- ⏳ 知识图谱存储
- ⏳ 细粒度权限控制
- ⏳ 分布式向量索引
- ⏳ LLM 驱动的记忆摘要优化
- ⏳ 跨会话记忆迁移

---

## 联系和支持

如有问题或建议，请参考：
- 模块文档：`app/core/memory_enhanced.py`
- 集成代码：`app/core/chat_graph.py`
- 测试用例：`tests/test_memory_enhanced.py` (待建)

