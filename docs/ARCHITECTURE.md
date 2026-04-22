# Pithy Agent 运行逻辑详细文档

> 版本: 0.1.0 | 更新日期: 2026-04-20

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [启动流程](#2-启动流程)
3. [请求处理管线](#3-请求处理管线)
4. [核心推理引擎](#4-核心推理引擎)
5. [工具系统](#5-工具系统)
6. [记忆系统](#6-记忆系统)
7. [安全体系](#7-安全体系)
8. [会话管理](#8-会话管理)
9. [Skill 编排引擎](#9-skill-编排引擎)
10. [数据持久化](#10-数据持久化)
11. [API 端点清单](#11-api-端点清单)
12. [数据流图](#12-数据流图)

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    客户端层                               │
│          Electron App / Web Browser / API Client          │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / SSE
┌───────────────────────▼─────────────────────────────────┐
│                   FastAPI 应用层                          │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌─────────────┐ │
│  │ 中间件链  │ │ 路由分发  │ │ 认证守卫 │ │  输入守卫   │ │
│  │ Trace ID │ │ Rate Limit│ │ Token   │ │ Injection   │ │
│  │ Security │ │ CORS     │ │ Lockout │ │ Sanitize    │ │
│  └──────────┘ └──────────┘ └─────────┘ └─────────────┘ │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                    推理引擎层                              │
│  ┌─────────────────────────────────────────────────┐    │
│  │  PlannerExecutorEngine (首选, 流式)              │    │
│  │  retrieve → plan → execute → synthesize → update │    │
│  ├─────────────────────────────────────────────────┤    │
│  │  ChatGraphEngine (LangGraph ReAct, 非流式/回退)   │    │
│  │  retrieve → reason → update                     │    │
│  └─────────────────────────────────────────────────┘    │
└───────┬──────────────┬──────────────┬───────────────────┘
        │              │              │
┌───────▼───┐  ┌───────▼───┐  ┌──────▼────┐
│  LLM 客户端│  │  工具系统   │  │  记忆系统  │
│  OpenAI   │  │  Builtin   │  │  Enhanced │
│  通义千问  │  │  Custom    │  │  Hierarchy│
│  文心一言  │  │  MCP       │  │  Reflect  │
│  Mock     │  │  Registry  │  │  Dedupe   │
└───────┬───┘  └───────┬───┘  └──────┬────┘
        │              │              │
┌───────▼──────────────▼──────────────▼───────────────────┐
│                   持久化层                                │
│           SQLite (WAL) + AES-256-GCM 加密配置             │
│  conversations | memory_items | chat_sessions | kv_store │
│  token_usage | skills | custom_tools | mcp_servers       │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 启动流程

### 2.1 模块初始化顺序

```
app/main.py 顶层执行（模块加载时）:
1. 日志系统初始化
   └─ JSON 格式日志 → logs/agent.log
   └─ _JSONFormatter: {ts, level, logger, msg, trace_id, exception}

2. FastAPI 应用创建（含 lifespan）
   └─ lifespan 上下文管理器:
       ├─ 启动: 验证 data/ 目录、DB schema、日志启动信息
       └─ 关闭: 断开所有MCP连接、日志关闭信息

3. 中间件注册（执行顺序: 从外到内）
   ├─ CORSMiddleware        — 跨域处理
   ├─ SecurityHeadersMiddleware — 安全头 (pure ASGI, SSE 安全)
   └─ RequestTraceMiddleware    — X-Trace-Id 注入

4. 核心组件实例化
   ├─ AppDB(data/agent.db)         — SQLite 数据库
   ├─ ConfigStore(db, secret.key)  — 加密配置管理
   ├─ LLMClient()                  — LLM 调用客户端
   ├─ EnhancedMemoryManager(db)    — 增强记忆系统
   ├─ ToolRegistry(db)             — 工具注册表 (含 MCP)
   ├─ LangChainAdapter(llm, tools) — LangChain 桥接
   ├─ ChatGraphEngine(adapter, mm) — LangGraph ReAct 引擎
   ├─ PlannerExecutorEngine(...)   — Planner/Executor 双 Agent
   └─ SkillRuntime(db, cfg, llm, tools) — Skill 执行器

5. 认证状态初始化
   └─ AUTH_STATE = {locked, token, failed_attempts, lockout_until, token_issued_at}
```

### 2.2 数据库 Schema 初始化

```
AppDB._init_schema() 创建 10 张表:
├─ kv_store              — 键值配置存储
├─ conversations         — 对话消息记录
├─ chat_sessions         — 会话元数据
├─ conversation_summaries — 会话摘要
├─ conversation_state    — 会话结构化状态
├─ memory_items          — 长期记忆 (含嵌入向量)
├─ tool_state            — 工具启用/禁用状态
├─ custom_tools          — 自定义工具 Manifest
├─ mcp_servers           — MCP 服务器配置
├─ skills / skill_versions — 技能定义及版本
└─ token_usage           — Token 使用量追踪
```

---

## 3. 请求处理管线

### 3.1 完整请求生命周期

```
客户端 POST /api/chat
  │
  ▼
RequestTraceMiddleware → 生成 trace_id, 注入 X-Trace-Id 响应头
  │
  ▼
SecurityHeadersMiddleware → 注入安全响应头 (CSP, X-Frame-Options...)
  │
  ▼
CORSMiddleware → 跨域校验
  │
  ▼
RateLimiter (slowapi) → 频率限制检查
  │
  ▼
_require_unlocked(request) → 认证/解锁检查
  │  ├─ 无密码: 直接通过
  │  ├─ 有密码+已解锁+Token有效: 通过
  │  └─ 否则: HTTP 423 Locked
  │
  ▼
InputGuard.check(message) → 输入安全检查
  │  ├─ 长度检查 (≤8000 字符)
  │  ├─ 提示注入检测 (20+ 正则模式)
  │  ├─ 控制 Token 剥离 (<|im_start|> 等)
  │  └─ 阻断 → HTTP 400 INPUT_BLOCKED
  │
  ▼
_ensure_session(session_id) → 会话管理
  │  ├─ session_id 格式校验 ([a-zA-Z0-9_-], ≤120字符)
  │  ├─ 空 → 自动生成 session_TIMESTAMP_RANDOM
  │  └─ 不存在 → 自动创建
  │
  ▼
推理引擎选择（按优先级）
  │  ├─ 1. PlannerExecutorEngine (流式 /api/chat/stream)
  │  ├─ 2. ChatGraphEngine (LangGraph, /api/chat)
  │  └─ 3. Legacy ReAct Loop (回退)
  │
  ▼
返回 ChatResponse / SSE stream
```

### 3.2 非流式 vs 流式对比

| 特性 | `POST /api/chat` | `POST /api/chat/stream` |
|---|---|---|
| 响应格式 | JSON `ChatResponse` | SSE `text/event-stream` |
| 推理引擎 | ChatGraphEngine → Legacy | PlannerExecutorEngine → Legacy |
| Token 流式 | ❌ 等待完整回复 | ✅ 逐 token 输出 |
| 进度反馈 | ❌ | ✅ step/thought/tool_done 事件 |
| 自动标题 | ✅ 同步生成 | ✅ 在 done 事件前生成 |

---

## 4. 核心推理引擎

### 4.1 引擎选择逻辑

```
# 流式端点 (/api/chat/stream):
if planner_executor_engine.available and not force_tool:
    → PlannerExecutorEngine (双 Agent, 流式)
else:
    → ChatGraphEngine (LangGraph ReAct)

# 非流式端点 (/api/chat):
→ ChatGraphEngine (LangGraph ReAct, 唯一路径)
```

### 4.2 PlannerExecutorEngine（双 Agent 架构）

```
LangGraph StateGraph:
  retrieve → plan → execute → synthesize → update → END

┌──────────────────────────────────────────────────────┐
│ Node: retrieve                                        │
│   1. 保存用户消息到 conversations 表                    │
│   2. EnhancedMemoryManager.retrieve_context()         │
│      ├─ 获取最近 6 条消息 (core layer)                  │
│      ├─ 获取会话摘要 (summary layer)                    │
│      ├─ 向量相似度检索长期记忆 (retrieval layer)         │
│      └─ 组合为 memory_prompt                           │
│   3. 输出: memory_ctx, memory_prompt                   │
└───────────────────────┬──────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────┐
│ Node: plan (Planner Agent)                            │
│   1. 构建 Planner Prompt:                              │
│      "你是任务规划器，把请求分解为步骤"                    │
│      + 可用工具列表 + 记忆上下文 + 用户请求               │
│   2. LLM 调用 (json_mode=True)                        │
│   3. 解析 JSON 计划:                                    │
│      {                                                 │
│        "reasoning": "分析思路",                         │
│        "steps": [                                      │
│          {"index":1, "task":"...", "type":"tool",       │
│           "tool":"web_search", "params":{"query":"..."}},│
│          {"index":2, "task":"...", "type":"llm"}        │
│        ]                                               │
│      }                                                 │
│   4. 步骤���限制: max_plan_steps=6                       │
│   5. 输出: plan[], plan_reasoning                      │
└───────────────────────┬──────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────┐
│ Node: execute (Executor Agent)                        │
│   遍历 plan 中每个 step:                                │
│                                                        │
│   type == "tool":                                      │
│     1. 解析参数, 补充上下文                              │
│     2. ToolRegistry.execute(tool_name, params)         │
│        ├─ ThreadPool 超时保护 (60s)                     │
│        ├─ 自动重试 (2 次, 指数退避)                      │
│        └─ 结果大小截断 (50KB)                            │
│     3. 失败自动重试 1 次 (500ms 间隔)                    │
│                                                        │
│   type == "llm":                                       │
│     1. 拼接上下文: 用户请求 + 记忆 + 前序步骤输出         │
│     2. LLM 推理调用                                     │
│     3. 记录 token 使用量                                 │
│                                                        │
│   输出: step_outputs[], executed_results[], react_trace │
└───────────────────────┬──────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────┐
│ Node: synthesize                                      │
│   单步骤且无工具 → 直接返回 LLM 输出                     │
│   多步骤 → 构建 Synthesize Prompt:                      │
│     "根据执行轨迹，给出最终回答"                          │
│     + 用户请求 + 记忆上下文 + 各步骤输出                  │
│   LLM 调用生成最终回答                                   │
│   输出: final_reply                                    │
└───────────────────────┬──────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────┐
│ Node: update                                          │
│   1. 保存 assistant 消息到 conversations                │
│   2. EnhancedMemoryManager.update_after_turn()        │
│      ├─ 添加情景记忆 (episodic)                         │
│      ├─ 提取事实/偏好记忆                               │
│      ├─ 更新结构化状态 (goals, pending_steps)           │
│      ├─ 刷新会话摘要 (每 20 条消息)                      │
│      ├─ 触发反思 (每 10 轮)                              │
│      ├─ 修剪长期记忆 (每 10 轮, 上限 600)                │
│      ├─ 清理过期记忆 (>30天 + 低重要度)                   │
│      └─ 归档旧消息 (>200 条自动清理)                     │
│   输出: memory_update                                  │
└──────────────────────────────────────────────────────┘
```

### 4.3 ReAct 推理循环（ChatGraphEngine）

> ReAct 逻辑统一由 `ChatGraphEngine`（LangGraph `StateGraph`）执行，
> 核心解析函数位于 `app/core/agent.py`。

```
ChatGraphEngine — LangGraph StateGraph:
  retrieve → reason → update → END

核心逻辑 (app/core/agent.py):
  ├─ build_react_system_prompt(tools)  — 构建含工具描述的 system prompt
  ├─ build_react_scratchpad(msg, trace) — 拼装 Question + Thought/Action/Observation
  └─ parse_react_llm_output(text, tools) — 解析 LLM 输出:
       ├─ "Final Answer:" → 停止, 返回最终回答
       ├─ "Action: <tool>" + "Action Input: <json>" → 执行工具
       │   ├─ 未知工具 → 自动停止 (防幻觉)
       │   └─ {{tool:xxx}} 引用 → 解析前序工具输出
       └─ 无结构化输出 → 作为最终回答返回

使用场景:
  /api/chat       → ChatGraphEngine (唯一路径)
  /api/chat/stream → PlannerExecutorEngine (首选)
                   → ChatGraphEngine (回退, 当 PlannerExecutor 不可用时)
```

### 4.4 SSE 事件类型

```
流式端点 /api/chat/stream 输出的事件:

{"type":"trace",      "trace_id":"abc123"}         — 推理追踪 ID
{"type":"step",       "step":"memory",  "detail":"记忆检索完成"} — 进度
{"type":"plan",       "reasoning":"...", "steps":[...]}  — 计划生成
{"type":"step_start", "index":1, "task":"...", "step_type":"tool"} — 步骤开始
{"type":"step",       "step":"tool",    "detail":"调用工具: web_search"} — 工具调用
{"type":"step_done",  "index":1, "tool":"web_search", "output":"..."} — 步骤完成
{"type":"step",       "step":"thought", "detail":"..."}  — 推理过程
{"type":"token",      "text":"你"}                       — 流式 token
{"type":"done",       "session_id":"...", "session_name":"...",
                      "trace_id":"...", "token_usage":{...}} — 完成
{"type":"error",      "message":"..."}                    — 错误
```

---

## 5. 工具系统

### 5.1 工具来源

```
ToolRegistry 管理三类工具:

1. Builtin (内置)
   ├─ read_file      — 读取本地文件 (≤10MB, 文本)
   ├─ write_file     — 写入文件 (仅限 data/ 目录)
   ├─ json_parse     — 解析 JSON
   ├─ web_search     — DuckDuckGo 搜索
   ├─ run_command    — 执行命令 (high risk, 黑名单+注入检测)
   ├─ echo           — 回显消息
   ├─ ocr_image      — Tesseract OCR
   ├─ sqlite_query   — 只读 SQL 查询 (PRAGMA query_only=ON)
   ├─ capture_screenshot — 截图 (high risk)
   └─ http_request   — HTTP 请求 (SSRF 防护, 5MB 限制)

2. Custom (自定义 Manifest)
   └─ 基于 Manifest 声明, 映射到某个 builtin 工具
      ├─ default_params: 预设参数
      └─ param_mapping: 参数重命名

3. MCP (Model Context Protocol)
   ├─ HTTP transport: JSON-RPC 2.0 over HTTP
   └─ Stdio transport: JSON-RPC 2.0 over stdin/stdout
       ├─ 自动发现工具 (tools/list)
       ├─ 自动重连 (执行失败时)
       └─ 优雅关闭 (应用退出时)
```

### 5.2 工具执行流程

```
ToolRegistry.execute(name, params, authorized)
  │
  ├─ 1. 工具查找: MCP → Builtin → Custom
  ├─ 2. 启用检查: db.is_tool_enabled()
  ├─ 3. 权限检查: high risk 需要 authorized=True
  ├─ 4. Custom Manifest 参数合并
  │
  ▼
  ThreadPoolExecutor (timeout=60s)
  │
  ├─ 执行工具 handler(params)
  ├─ 结果包装: MCPToolResult.from_dict()
  ├─ 结果截断: _cap_result_size (50KB)
  │
  ├─ 错误处理:
  │   ├─ PermissionError/KeyError → 不重试
  │   ├─ ConnectionError/TimeoutError → 重试 (最多 2 次, 指数退避)
  │   └─ 其他异常 → 友好错误消息
  │
  └─ 返回标准化结果 dict
```

### 5.3 安全防护

```
命令执行 (run_command):
  ├─ 黑名单检测: rm -rf, shutdown, curl, python -c, nc...
  ├─ 注入模式检测: $(), ``, |sh, ;rm, &&rm
  ├─ 路径穿越检测: ../, ..\\
  ├─ 归一化: 去除引号/反引号/^ 绕过
  └─ Linux: shlex.split() + shell=False

SQL 查询 (sqlite_query):
  ├─ 仅允许 SELECT/PRAGMA
  ├─ 禁止分号 (多语句注入)
  └─ PRAGMA query_only=ON

HTTP 请求 (http_request):
  ├─ SSRF: 阻止私有 IP / 回环 / 链路本地 / 保留地址
  ├─ SSRF: DNS 解析失败 → 拒绝 (非放行)
  ├─ 响应体限制: 5MB
  └─ 超时: ≤60s

文件写入 (write_file):
  └─ 仅允许 data/ 目录
```

---

## 6. 记忆系统

### 6.1 三层分层架构

```
EnhancedMemoryManager 实现三层记忆:

Layer 1: Core (核心层)
  ├─ 最近 6 条消息
  ├─ Token 预算: 2000
  └─ 来源: conversations 表

Layer 2: Summary (摘要层)
  ├─ 自动生成的会话摘要
  ├─ 触发条件: 消息数 ≥ 20
  └─ 来源: conversation_summaries 表

Layer 3: Retrieval (检索层)
  ├─ 向量相似度检索长期记忆
  ├─ Token 预算: 3000
  ├─ 动态 K: simple=2, medium=4, complex=8
  └─ 来源: memory_items 表
```

### 6.2 记忆类型

```
MemoryType:
  ├─ episodic    — 情景记忆: 每轮对话自动记录
  ├─ semantic    — 语义记忆: 知识规则
  ├─ preference  — 偏好记忆: "我喜欢...", "请默认..."
  ├─ fact        — 事实记忆: "我的名字是...", "项目路径是..."
  ├─ reflection  — 反思记忆: Agent 自我总结 (每 10 轮)
  └─ error       — 错误记忆: 失败记录
```

### 6.3 记忆评分公式

```
MemoryScore.composite() =
    importance × 0.35     // 基础重要度 [0,1]
  + recency    × 0.25     // 时间衰减: 2^(-age_days / 7)
  + relevance  × 0.25     // 余弦相似度 (查询嵌入 vs 记忆嵌入)
  + access_freq × 0.10    // 归一化访问次数
  + consistency × 0.05    // 去重分数
```

### 6.4 记忆生命周期

```
update_after_turn() 每轮对话后执行:

1. 记录情景记忆 (episodic)
   └─ 文本 = "用户: {msg}\n助手: {reply}", importance=0.6

2. 提取事实/偏好记忆 (正则匹配)
   ├─ "我喜欢..." → preference, importance=0.75
   └─ "我的XX是..." → fact, importance=0.70

3. 更新结构化状态
   ├─ goals: 包含 "目标/goal/我要" 的消息
   └─ pending_steps: 包含 "然后/接下来" 的消息

4. 刷新摘要 (每 20 条消息)

5. 触发反思 (每 10 轮)
   └─ 分析成功/失败/模式 → reflection 记忆

6. 修剪 (每 10 轮, 节流)
   ├─ _prune_long_term_memory: 超过 600 条 → 按评分批量删除
   └─ _clean_stale_memories: >30 天 + importance<0.15 → 批量删除

7. 归档旧消息 (>200 条自动清理最旧的)
```

### 6.5 嵌入向量

```
优先级:
  1. sentence-transformers (all-MiniLM-L6-v2) — 本地, 384 维
  2. Hash-based pseudo-embedding — 回退, 64 维

余弦相似度计算:
  1. numpy (如可用) — ~50x 加速
  2. 纯 Python 回退

find_similar_memories():
  SQL 过滤: importance ≥ 0.2 AND embedding 非空
  Python 排序: sim*0.85 + importance*0.1 + recency_bonus*0.05
  返回 top_k (默认 6)
```

---

## 7. 安全体系

### 7.1 认证流程

```
启动密码保护 (可选):

1. 首次设置: POST /api/security/setup {password}
   └─ PBKDF2-SHA256, 200K 迭代, 16B 盐

2. 解锁: POST /api/security/unlock {password}
   ├─ 验证: hmac.compare_digest (时序安全)
   ├─ 成功: 返回 token (secrets.token_urlsafe(24))
   ├─ 失败: failed_attempts++
   └─ 5 次失败: 锁定 60 秒

3. 每次请求: X-Session-Token 头校验
   └─ Token 有效期: 24 小时

4. 锁定: POST /api/security/lock
   └─ 清除 token, 设置 locked=True
```

### 7.2 配置加密

```
ConfigStore 使用 AES-256-GCM:

secret.key → SHA-256 → 32 字节密钥
加密: AES-GCM(nonce=12B) → base64(nonce + tag + ciphertext)
解密: 逆操作

加密字段: api_key, secret_key
文件权限: chmod 600 (Unix)
```

### 7.3 输入防护

```
InputGuard.check():
  ├─ 长度: ≤8000 字符
  ├─ 注入检测: 20+ 模式
  │   ├─ "ignore previous instructions"
  │   ├─ <|im_start|>, [INST], <<SYS>>
  │   ├─ "jailbreak mode", "DAN"
  │   ├─ "reveal your prompt"
  │   └─ "decode base64 and execute"
  └─ Token 剥离: 去除控制 token

InputGuard.sanitize_output():
  └─ XSS 防护: <script>, onclick=, javascript:, <iframe>
```

### 7.4 中间件链

```
请求 → RequestTraceMiddleware (trace_id)
     → SecurityHeadersMiddleware (CSP, X-Frame-Options, etc.)
     → CORSMiddleware (白名单 origins)
     → RateLimiter (slowapi)
     → 全局异常处理 (JSON 500, 非 HTML)
     → 路由处理
```

---

## 8. 会话管理

### 8.1 会话生命周期

```
创建:
  ├─ 自动: 首次 chat 时, session_id = session_{timestamp}_{random}
  └─ 手动: POST /api/sessions {session_id, name}

使用:
  ├─ 消息持久化: conversations 表
  ├─ 自动标题: 前 4 条消息后 LLM 生成 (5~12 字)
  └─ 上下文: 最近 6 条消息 + 摘要 + 长期记忆

导出:
  ├─ Markdown: # 对话记录 + 角色/时间/内容
  └─ JSON: [{role, content, created_at}]

删除 (级联):
  ├─ conversations
  ├─ conversation_summaries
  ├─ conversation_state
  ├─ memory_items
  ├─ token_usage
  └─ chat_sessions
```

### 8.2 Token 使用量追踪

```
每次 LLM 调用记录:
  {session_id, trace_id, provider, model,
   prompt_tokens, completion_tokens, total_tokens, latency_ms}

查询接口:
  GET /api/sessions/{id}/stats → 单会话统计
  GET /api/stats/tokens        → 全局统计
```

---

## 9. Skill 编排引擎

### 9.1 Skill 定义

```json
{
  "name": "summarize_and_save",
  "version": "1.0.0",
  "description": "搜索并保存摘要",
  "steps": [
    {"kind": "tool", "name": "web_search", "params": {"query": "..."}},
    {"kind": "llm",  "name": "summarize",  "params": {"prompt": "总结以上内容"}},
    {"kind": "tool", "name": "write_file", "params": {"path": "data/summary.md"}}
  ]
}
```

### 9.2 执行流程

```
SkillRuntime.run(skill_id, input_text, context):
  遍历 steps:
    kind=="tool" → ToolRegistry.execute(name, params)
    kind=="llm"  → LLMClient.call(prompt, cfg, context)
  每步输出 → 下一步的 input_text
  返回: {skill, output, steps[]}
```

### 9.3 版本管理

```
skill_versions 表记录每次保存:
  ├─ source: api_save / import / package_import / rollback
  └─ rollback: 指定 version_id 回滚, 创建新版本记录
```

---

## 10. 数据持久化

### 10.1 SQLite 优化

```
PRAGMA:
  ├─ journal_mode=WAL  — 写时复制, 读写并发
  └─ synchronous=NORMAL — 平衡性能与安全

连接管理:
  ├─ 线程本地 (threading.local)
  ├─ 连接健康检查 (SELECT 1)
  └─ 自动重建断开的连接

Schema 版本: kv_store.schema_version = 2

索引:
  ├─ idx_conversations_session_id (session_id, id DESC)
  ├─ idx_memory_items_session (session_id, created_at DESC)
  ├─ idx_token_usage_session (session_id, created_at DESC)
  └─ idx_skill_versions_skill_id (skill_id, version_id DESC)
```

### 10.2 加密存储

```
kv_store 中的敏感数据:
  ├─ model_config → api_key_encrypted, secret_key_encrypted (AES-256-GCM)
  └─ auth_password_hash → PBKDF2-SHA256 (salt + hash + iterations)
```

---

## 11. API 端点清单

### 系统
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | Web UI 入口 |
| GET | `/api/health` | 健康检查 (CPU/内存/消息数) |
| GET | `/api/release/info` | 版本/构建/签名信息 |
| GET | `/api/logs` | 查看日志 (尾部读取, 过滤, 脱敏) |

### 安全
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/security/status` | 密码/锁定状态 |
| POST | `/api/security/setup` | 首次设置密码 |
| POST | `/api/security/unlock` | 解锁 (含防暴力破解) |
| POST | `/api/security/lock` | 锁定 |

### 配置
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/settings` | 应用设置 |
| PUT | `/api/settings` | 保存设置 |
| GET | `/api/config/model` | 模型配置 (脱敏) |
| PUT | `/api/config/model` | 保存模型配置 |
| POST | `/api/config/model/test` | 测试模型连接 |

### 对话
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 非流式对话 |
| POST | `/api/chat/stream` | 流式对话 (SSE) |
| GET | `/api/history` | 会话消息历史 |

### 会话
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sessions` | 会话列表 |
| POST | `/api/sessions` | 创建会话 |
| PATCH | `/api/sessions/{id}` | 重命名 |
| DELETE | `/api/sessions/{id}` | 删除 (级联) |
| GET | `/api/sessions/{id}/export` | 导出 (MD/JSON) |
| POST | `/api/sessions/{id}/generate-title` | 手动生成标题 |
| GET | `/api/sessions/{id}/stats` | Token 使用统计 |
| GET | `/api/stats/tokens` | 全局 Token 统计 |

### 工具
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tools` | 工具列表 (含 OCR 状态) |
| PATCH | `/api/tools/{name}` | 启用/禁用 |
| POST | `/api/tools/{name}/execute` | 直接执行工具 |
| GET | `/api/tools/manifests` | 自定义工具列表 |
| POST | `/api/tools/import` | 导入工具 Manifest |
| DELETE | `/api/tools/custom/{name}` | 删除自定义工具 |
| GET | `/api/tools/ocr/status` | OCR 可用性 |

### MCP
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/mcp/servers` | MCP 服务器列表 |
| POST | `/api/mcp/servers` | 注册 MCP 服务器 |
| POST | `/api/mcp/servers/{id}/refresh` | 刷新工具发现 |
| DELETE | `/api/mcp/servers/{id}` | 删除 MCP 服务器 |

### Skills
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/skills` | Skill 列表 |
| POST | `/api/skills` | 创建/更新 Skill |
| PATCH | `/api/skills/{id}` | 启用/禁用 |
| DELETE | `/api/skills/{id}` | 删除 |
| POST | `/api/skills/{id}/run` | 执行 Skill |
| POST | `/api/skills/import` | 导入 (JSON/YAML) |
| GET | `/api/skills/{id}/export` | 导出 |
| POST | `/api/skills/import/package` | 批量导入 (ZIP) |
| GET | `/api/skills/{id}/versions` | 版本列表 |
| POST | `/api/skills/{id}/rollback` | 版本回滚 |

---

## 12. 数据流图

### 12.1 完整对话数据流

```
用户输入 "搜索 Python 最新版本并保存到文件"
  │
  ▼ POST /api/chat/stream
  │
  ├─ InputGuard.check() → 通过
  ├─ _ensure_session("session_xxx")
  │
  ▼ PlannerExecutorEngine.stream_events()
  │
  ├─ [retrieve] 检索记忆
  │   ├─ 最近消息: []
  │   ├─ 摘要: ""
  │   └─ 长期记忆: ["用户偏好: 保存为 Markdown"]
  │   → SSE: {"type":"step","step":"memory","detail":"记忆检索完成"}
  │
  ├─ [plan] LLM 生成计划
  │   → plan = [
  │       {index:1, task:"搜索Python最新版本", type:"tool", tool:"web_search"},
  │       {index:2, task:"保存到文件",          type:"tool", tool:"write_file"},
  │       {index:3, task:"总结回复",            type:"llm"}
  │     ]
  │   → SSE: {"type":"plan","steps":[...],"reasoning":"..."}
  │
  ├─ [execute]
  │   ├─ Step 1: web_search(query="Python latest version")
  │   │   → SSE: {"type":"step_start","index":1}
  │   │   → SSE: {"type":"step_done","index":1,"output":"Python 3.13..."}
  │   │
  │   ├─ Step 2: write_file(path="data/python.md", content=...)
  │   │   → SSE: {"type":"step_start","index":2}
  │   │   → SSE: {"type":"step_done","index":2,"output":"written 256 bytes"}
  │   │
  │   └─ Step 3: LLM 总结
  │       → SSE: {"type":"step_start","index":3}
  │       → SSE: {"type":"step_done","index":3,"output":"已为您..."}
  │
  ├─ [synthesize] 合成最终回答
  │   → SSE: {"type":"token","text":"已"}
  │   → SSE: {"type":"token","text":"为"}
  │   → SSE: {"type":"token","text":"您"}
  │   → ...
  │
  ├─ [update] 持久化
  │   ├─ 保存消息到 DB
  │   ├─ 添加 episodic 记忆
  │   ├─ 提取 fact: "Python 最新版本 3.13"
  │   └─ 更新 token_usage
  │
  └─ SSE: {"type":"done","session_id":"...","session_name":"Python版本查询",
           "token_usage":{"prompt_tokens":850,"completion_tokens":200}}
```

---

## 附录: 文件结构

```
app/
├─ main.py                    # FastAPI 应用, 路由, 中间件 (~1580 行)
├─ schemas.py                 # Pydantic 请求/响应模型
├─ __init__.py
├─ core/
│  ├─ agent.py                # ReAct 解析器, 计划构建器
│  ├─ chat_graph.py           # LangGraph ReAct 引擎
│  ├─ chat_graph_planner.py   # Planner/Executor 双 Agent 引擎
│  ├─ config_store.py         # 加密配置管理 (AES-256-GCM)
│  ├─ db.py                   # SQLite 数据库层
│  ├─ embeddings.py           # 嵌入向量 + 余弦相似度
│  ├─ input_guard.py          # 输入安全守卫
│  ├─ langchain_adapter.py    # LangChain 桥接
│  ├─ llm.py                  # LLM 客户端 (多 Provider)
│  ├─ llm_errors.py           # LLM 错误类型
│  ├─ memory.py               # 基础记忆管理器
│  └─ memory_enhanced.py      # 增强记忆 (分层/评分/反思/去重)
├─ tools/
│  ├─ base.py                 # 工具数据类型定义
│  ├─ builtin.py              # 内置工具实现
│  ├─ mcp_client.py           # MCP 客户端 (HTTP/Stdio)
│  └─ registry.py             # 工具注册表 + 执行器
├─ skills/
│  └─ runtime.py              # Skill 编排运行时
└─ static/
   ├─ index.html, app.js, styles.css  # Web UI
   └─ build-info.json
```

