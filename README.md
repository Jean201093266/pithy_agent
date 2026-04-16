# Pithy Local Agent (MVP)

本项目是基于开发方案实现的本机部署 Agent 最小可运行版本，包含：
- 本地 FastAPI 服务（核心逻辑层）
- 可视化页面（交互、API 配置、工具/技能、设置中心、日志中心）
- 工具调用（文件读写、搜索、JSON 解析、命令执行）
- 结构化 Agent Brain（ReAct 主策略 + 轻量 Plan-Exec 增强）
- 技能定义与执行（YAML/JSON 可扩展）
- 本地 SQLite 存储、日志、基础加密存储
- 启动密码/解锁机制、主题切换、中英文界面
- 技能可视化编辑器（步骤可视化添加、生成 SkillSpec JSON）
- 扩展工具：OCR 图像识别、SQLite 只读查询、截图工具
- Electron 桌面壳（本机桌面窗口 + 自动拉起后端）

## 目录结构

- `app/main.py` FastAPI 入口
- `app/core/` 配置、LLM 适配、数据库、规划逻辑
- `app/tools/` 工具注册与内置工具
- `app/skills/` 技能运行时
- `app/static/` 前端静态页面
- `tests/` 冒烟测试与核心单元测试
- `run.py` 本地启动脚本

## 快速开始

1) 安装依赖

```powershell
cd D:\projects\github\pithy_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) 启动服务

```powershell
python run.py
```

3) 打开页面

- 浏览器访问 `http://127.0.0.1:8000`

## 运行测试

```powershell
cd D:\projects\github\pithy_agent
.\.venv\Scripts\Activate.ps1
pytest -q
```

## 说明

- 默认 `provider=mock`，无需真实 API key 即可演示完整流程。
- 支持的 `provider`：`mock`、`openai`、`openai-compatible`、`tongyi`、`wenxin`。
- OpenAI/OpenAI-compatible：配置 `model` + `api_key`，`openai-compatible` 还需 `base_url`。
- 通义千问（Tongyi）：配置 `provider=tongyi` + `api_key`，默认使用 DashScope 兼容模式地址。
- 文心（Wenxin）：配置 `provider=wenxin` + `api_key` + `secret_key`（自动换取 access token）。
- 高危工具 `run_command` 需要显式授权参数（后端接口 `authorized=true`）。

## 安全与设置中心

- 首次使用可设置启动密码；设置后每次锁定/重启后需先解锁。
- 支持 `light / dark / system` 三种主题模式。
- 支持 `zh-CN / en-US` 界面语言切换。
- 设置中心可持久化保存日志条数、日志级别、自动刷新和发送快捷键。
- 锁定状态下会阻止聊天、技能运行、工具执行、模型配置修改等敏感操作。

## 日志中心

- 支持按最近 N 条查看日志。
- 支持按日志级别筛选：`INFO / WARNING / ERROR`。
- 支持关键词搜索。
- 返回日志时会对 `api_key / secret_key / password / Bearer token` 做基础脱敏。

## LLM 错误码

LLM 相关接口（如 `/api/chat`、`/api/config/model/test`）在失败时返回结构化错误：

- `LLM_CONFIG_ERROR` 配置缺失或非法
- `LLM_AUTH_ERROR` 鉴权失败（如 key 无效）
- `LLM_RATE_LIMIT` 触发限流
- `LLM_TIMEOUT` 请求超时
- `LLM_NETWORK_ERROR` 网络异常
- `LLM_UPSTREAM_ERROR` 上游服务错误
- `LLM_RESPONSE_ERROR` 响应解析失败

## 技能导入导出与回滚 API

- `POST /api/skills/import`：导入技能定义（`format=json|yaml|auto` + `content`）。
- `GET /api/skills/{skill_id}/export?format=json|yaml[&version_id=...]`：导出当前或指定版本技能。
- `GET /api/skills/{skill_id}/versions`：查看技能历史版本。
- `POST /api/skills/{skill_id}/rollback`：按 `target_version_id` 回滚技能当前版本。

前端页面也已提供对应入口：
- 版本下拉选择回滚（也支持手动输入 `version_id` 覆盖下拉）
- 导出内容一键复制
- 导出内容一键下载（`json/yaml`）

## Agent Brain

- 聊天接口会输出结构化 `brain` 信息，包括：
  - `intent`
  - `plan`
  - `tool_calls`
  - `confidence`
  - `executed_tools`
- 执行策略：`ReAct`（Thought-Action-Observation）为主，`Plan-Exec` 轻量计划为增强。
- `brain.react_trace` 提供每轮思考、动作和观察轨迹，便于调试与审计。
- 当前已支持的典型规划场景：
  - 搜索信息
  - 读取文件
  - 写入文件
  - 搜索并保存到文件
  - JSON 解析
  - 运行本地命令

## 自定义工具扩展

- 支持通过声明式 manifest 导入自定义工具。
- 当前实现采用“受控代理”模式：自定义工具不会直接上传任意 Python 代码，而是映射到已注册的内置工具。
- 已提供前端入口：
  - 导入自定义工具 manifest
  - 查看已导入工具清单
  - 执行指定工具并查看结果

示例 manifest：

```json
{
  "name": "custom_echo",
  "description": "Echo wrapper",
  "risk_level": "normal",
  "target_tool": "echo",
  "default_params": {
	"message": "hello"
  },
  "param_mapping": {
	"text": "message"
  },
  "version": "1.0.0"
}
```

## 新增工具能力

- `ocr_image`：对图片执行 OCR（依赖 `pytesseract` + `Pillow`，本机需可用 tesseract 引擎）。
- `sqlite_query`：执行只读 SQLite 查询（仅允许 `SELECT/PRAGMA`）。
- `capture_screenshot`：抓取本机截图并保存。
- 页面可查看 OCR 可用性状态（是否可用、原因、安装提示）。

## 技能可视化编辑器

- 页面内可直接填写：技能名、版本、描述。
- 可视化添加步骤（`llm` / `tool`）及 JSON 参数。
- 一键生成标准 `SkillSpec` JSON 后可直接保存。
- 新增步骤操作：复制步骤、上移/下移、删除步骤。
- 参数校验：`llm` 步骤要求 `prompt` 字段，非对象参数会被阻断。

## Electron 桌面模式

```powershell
cd D:\projects\github\pithy_agent
npm install
npm run desktop:start
```

说明：桌面模式会自动启动本地 Python 后端并加载 `http://127.0.0.1:8000`。

## Electron 打包（Windows）

```powershell
cd D:\projects\github\pithy_agent
npm install
npm run release:prepare
npm run dist:win
npm run release:artifacts
npm run release:verify
```

如需强制签名门禁校验：

```powershell
cd D:\projects\github\pithy_agent
npm run release:verify:signed
```

如仅验证打包目录产物（不生成安装包），可执行：

```powershell
cd D:\projects\github\pithy_agent
npm run release:prepare
npm run dist:win:dir
```

如需签名构建（证书环境变量已配置）：

```powershell
cd D:\projects\github\pithy_agent
npm run dist:win:signed
```

打包完成后，关键产物位于 `dist/`：

- `dist/PithyLocalAgent-Setup-0.1.0.exe`（NSIS 安装包）
- `dist/win-unpacked/`（免安装目录版）
- `dist/release-manifest.json`（版本/时间/提交信息）
- `dist/checksums.txt`（发布校验和）
- `dist/publish-index.json`（发布索引）
- `dist/upload-assets.json`（上传资产清单）

## 版本与更新日志自动注入

`npm run release:prepare` 会自动：

- 读取 `package.json` 版本号
- 生成 `app/static/build-info.json`
- 生成 `dist/release-manifest.json`
- 向 `RELEASE_NOTES.md` 追加当前版本发布块（若不存在）

发布前可手动完善 `RELEASE_NOTES.md` 的 Highlights。

## 发布清单模板

- 发布检查清单位于：`docs/release/CHECKLIST.md`
- 建议每次发版按清单执行：测试 -> 打包 -> 安装冒烟 -> 发布

## 签名与升级渠道说明

- 详细说明位于：`docs/release/SIGNING_AND_UPDATES.md`
- 一键本地发布流水线（打包 + 元数据校验）：

```powershell
cd D:\projects\github\pithy_agent
npm run release:pipeline
```

GitHub Releases 上传（可选）：

```powershell
cd D:\projects\github\pithy_agent
set GITHUB_TOKEN=ghp_xxx
set GITHUB_OWNER=your-owner
set GITHUB_REPO=your-repo
npm run release:upload:github
```

如需手动清理历史产物再打包，可执行：

```powershell
cd D:\projects\github\pithy_agent
npm run clean:dist
```

### 打包与安装前置条件

- 需要本机可用 `python` 命令（Electron 壳会拉起 `run.py`）。
- 首次运行 OCR 功能时，需要本机安装 Tesseract OCR 并加入 `PATH`。
- 当前安装包默认不签名，Windows 可能提示“未知发布者”，属于预期行为。

### 常见问题

- 若打包报签名/权限错误，可确认 `CSC_IDENTITY_AUTO_DISCOVERY=false` 已生效。
- 若桌面端打开后页面空白，先在终端执行 `python run.py` 验证后端可启动。
- 若 OCR 报不可用，请在页面“工具管理 -> OCR 状态”查看具体提示。

## E2E 测试（Playwright）

```powershell
cd D:\projects\github\pithy_agent
npm install
npx playwright install chromium
npm run test:e2e
```

