# 星舵 Helm

AI 任务编排与执行系统：用户提出需求 → Designer（LLM）生成技术设计书 → 用户审批 → 生成执行计划 → Executor 按步骤调用工具执行 → 全程 SQLite 持久化 + React 仪表盘可视化。

## 当前状态（2026-09-10）

核心链路完整可用：设计书生成与审批、执行计划生成、计划执行与实时日志、执行控制（暂停/恢复/跳步/回滚）、审计日志、配置管理。产品运行模式使用真实 LLM；凭据只从环境变量或密钥服务读取。错误处理遵循明确契约（见 [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md)）。

- 默认离线后端回归（CI 同款）：`python -m pytest -q -m "not integration and not browser" --ignore=tests/python/test_exec_e2e.py --ignore=tests/python/test_browser_check.py`（本轮验证 635 passed / 9 skipped）
- 前端测试：`vitest` 36 passed；production build 成功
- 真实 LLM 任务与隔离 checkpoint 验收：见 [docs/archive/checkpoint-recovery-acceptance-2026-09-09.md](docs/archive/checkpoint-recovery-acceptance-2026-09-09.md)（历史记录）
- CI：Python 离线回归、frontend test/build、Rust workspace test；真实 LLM 和浏览器测试需单独配置环境
- Rust：本机已装 cargo 1.98 + VS BuildTools 2022（MSVC v143 + Windows SDK，位于 `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`）；`cargo test --workspace` 本机复验通过（42 passed / 0 failed）。注意必须在 VS 原生 shell（先 `call Common7\Tools\VsDevCmd.bat`）里跑，否则 Git Bash 的 `/usr/bin/link` 会遮蔽 MSVC `link.exe`

- 超级搜索工具：super_search（多引擎聚合搜索 + 深度检索 + 正文抽取；无 Key 可用，可选 Tavily/Brave/Serper 增强）
- 代码上下文工具：code_context（Aider 式仓库地图 + SQLite FTS5 全文检索 + 知识笔记长期记忆，参考 docs/CODE_CONTEXT.md）
详细完成度与遗留事项见 [docs/STATUS.md](docs/STATUS.md)。

## 快速开始

### 1. 启动后端（端口 8000）

```bash
python scripts/start_api.py
```

- 所有 `/api/*` 端点需要 token：`Authorization: Bearer <token>` 或 `X-API-Token: <token>`
- token 来源：环境变量 `EAS_API_TOKEN`，否则读 `config/api_token`（首次启动自动生成）

### 2. 启动前端（端口 3000）

```bash
cd frontend
npm install
npm run dev
```

开发服务器把 `/api` 和 `/ws` 代理到 8000 端口。打开 **http://localhost:3000** 即可使用。

Windows 根目录只保留三个启动入口：

| 文件 | 作用 |
|------|------|
| `启动完整系统.bat` | 后端 8000 + 前端 3000 |
| `启动后端.bat` | 仅 `python scripts/start_api.py` |
| `启动前端.bat` | 仅 `frontend` 开发服 |

重启后端、热重载开发、打开浏览器分别在 `scripts/restart_api.bat`、`scripts/dev_reload.bat`、`scripts/open-helm.cmd`。旧的一键/菜单/9000 端口脚本已进 `docs/archive/startup/`。

### 3.（可选）CLI 设计助手

```bash
python scripts/cli.py
```

仅做设计书/计划生成，不执行。界面版功能更全。

## 运行测试

```bash
# 后端（必须在仓库根目录运行，pytest.ini 的 testpaths 指向 tests/）
python -m pytest

# 前端
cd frontend && npm test

# Rust
cargo test --workspace
```

## 项目结构

```
harness/
├── python/            # 后端核心
│   ├── api/server.py  # FastAPI 服务（核心逻辑所在）
│   ├── database.py    # SQLite 持久化
│   ├── tools/         # 10 个内置工具
│   ├── llm_client/    # 多提供商 LLM 客户端
│   └── ...
├── frontend/          # React 18 + Vite + Tailwind + Zustand
├── rust/              # Rust 组件（dispatcher/db/logger 等）
├── tests/             # pytest 测试（从仓库根目录运行）
├── scripts/           # 启动/检查/测试脚本
├── config/            # models.yaml、api_token 等
└── docs/              # 文档（历史报告在 docs/archive/）
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/STATUS.md](docs/STATUS.md) | 当前完成度与待办 |
| [docs/API.md](docs/API.md) | 后端 API 参考 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 开发环境与测试指南 |
| [docs/STRUCTURE.md](docs/STRUCTURE.md) | 目录结构与代码分布 |
| [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md) | 错误处理契约（改错误路径前必读） |
| [docs/state-machine-design.md](docs/state-machine-design.md) | 计划状态机设计 |
| [docs/AGENT_HARNESS_DESIGN.md](docs/AGENT_HARNESS_DESIGN.md) | 主设计稿（当前权威） |

日期戳验收/修复报告、旧架构稿与一次性脚本已迁入 [docs/archive/](docs/archive/)，描述的是当时状态，不代表现状。
