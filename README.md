# EAS 2.0 Harness

AI 任务编排与执行系统：用户提出需求 → Designer（LLM）生成技术设计书 → 用户审批 → 生成执行计划 → Executor 按步骤调用工具执行 → 全程 SQLite 持久化 + React 仪表盘可视化。

## 当前状态（2026-08-29）

核心链路完整可用：设计书生成与审批、执行计划生成、计划执行与实时日志、执行控制（暂停/恢复/跳步/回滚）、审计日志、配置管理。错误处理遵循明确契约（见 [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md)）。

- 后端测试：`pytest` 244 通过 + 2 跳过（覆盖率约 48%）
- 前端测试：`vitest` 30 通过
- CI：三任务流水线（Rust / pytest / vitest）

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

Windows 下也可以直接双击 `启动完整系统.bat`（同时启动前后端），或分别用 `启动后端.bat` / `启动前端.bat`。

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
│   ├── tools/         # 7 个内置工具
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
| [开始使用_WEB界面.md](开始使用_WEB界面.md) | Web 界面使用指南（推荐入门） |
| [docs/STATUS.md](docs/STATUS.md) | 当前完成度与待办 |
| [docs/API.md](docs/API.md) | 后端 API 参考 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 开发环境与测试指南 |
| [docs/STRUCTURE.md](docs/STRUCTURE.md) | 目录结构与代码分布 |
| [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md) | 错误处理契约（改错误路径前必读） |
| [docs/state-machine-design.md](docs/state-machine-design.md) | 计划状态机设计 |
| [docs/NEW_ARCHITECTURE_DESIGN.md](docs/NEW_ARCHITECTURE_DESIGN.md) | 原始架构设计（历史参考） |

历史测试报告与早期使用说明在 [docs/archive/](docs/archive/)，描述的是当时状态，不代表现状。
