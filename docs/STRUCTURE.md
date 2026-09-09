# EAS 2.0 目录结构与运行边界

> 本文描述当前代码分布（2026-09-09）。设计愿景见 NEW_ARCHITECTURE_DESIGN.md。

```text
harness/
├── python/                         # 当前唯一实际运行时
│   ├── api/server.py               # FastAPI API、生命周期和执行协调入口
│   ├── api/auth.py                 # Bearer / X-API-Token 鉴权
│   ├── database.py                 # SQLite 持久化、事务和状态写入
│   ├── designer/__init__.py        # 真实 Designer 实现
│   ├── executor/__init__.py        # 真实 Executor 实现
│   ├── application/                # 应用服务边界（渐进式拆分）
│   ├── domain/                     # 纯领域规则（计划状态）
│   ├── repositories/               # Database 的计划 Repository facade
│   ├── tools/                      # 15 个已注册内置工具
│   ├── llm_client/                 # OpenAI 兼容 Provider 与离线 DeterministicProvider
│   ├── knowledge/                  # 知识注入
│   ├── mcp_client.py / mcp_server.py
│   └── skills_loader.py            # Skills 加载
├── frontend/                       # React 18 + Vite + Tailwind + Zustand
│   └── src/                        # 8 个主视图、控制台壳、活动流和审批 UI
├── rust/                           # 未来主实现候选，当前未接入 Python 主链路
├── tests/                          # pytest、集成和 Rust 测试
├── scripts/                        # 启动、检查和测试脚本
├── config/                         # 提交模板；本地凭据覆盖文件被 gitignore
├── data/                           # SQLite 数据与运行缓存
├── docs/                           # 设计、状态和开发文档
├── plugins/                        # 插件示例
└── schemas/                        # 设计书、执行计划和工具 JSON Schema
```

## 关键事实

- 后端主入口是 `python/api/server.py`，目前仍包含较多编排逻辑；`python/application/`、`python/domain/`、`python/repositories/` 是不改 schema 的渐进式拆分边界。
- Python 是当前唯一实际运行时。Rust crate 未接入 FastAPI 主链路，未来接管必须遵守 `docs/runtime-boundary.md` 与 schemas/ 中的契约。
- 工具实现位于 `python/tools/`，当前默认注册 15 个工具；根目录 `tools/` 主要是文档和示例。
- 前端 dev server 默认 3000，API 后端默认 8000，Vite 将 `/api` 和 `/ws` 代理到后端。
- 受保护 API 端点必须使用 `require_token`；GET `/` 是公开健康入口；WebSocket 通过 query token 校验。
- pytest 必须从仓库根目录运行，避免测试路径和 `tests/conftest.py` 的导入约定失效。
- `config/models.local.yaml`、`config/api_token`、前端本地环境文件不得提交；生产或共享环境的凭据应从环境变量或密钥服务注入。
