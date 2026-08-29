# EAS 2.0 目录结构与代码分布

> 本文档描述**实际**的代码分布（2026-08-29 核对）。设计愿景见
> [NEW_ARCHITECTURE_DESIGN.md](NEW_ARCHITECTURE_DESIGN.md)，两者有出入的地方以本文为准。

```
harness/
├── python/                        # 后端核心（主链路都在这里）
│   ├── api/
│   │   ├── server.py              # ★ FastAPI 服务 + Designer/Executor 主逻辑（约 1500 行）
│   │   ├── auth.py                # token 鉴权（Bearer / X-API-Token）
│   │   ├── main.py                # 遗留 9000 端口旧入口，未使用，待清理
│   │   └── server.py.bak          # 备份文件，待清理
│   ├── database.py                # ★ SQLite 持久化（计划/步骤/日志/审计/回滚分支）
│   ├── tools/                     # ★ 7 个内置工具实现
│   │   ├── file_creator.py  command_runner.py  package_installer.py
│   │   ├── git_operator.py  text_editor.py     web_fetcher.py
│   │   ├── manual_tool.py
│   │   └── __init__.py            # ToolRegistry（默认注册 7 个工具）
│   ├── llm_client/                # 多提供商 LLM 客户端（含 DeterministicProvider 供测试）
│   ├── cache.py                   # 设计书/计划缓存（LRU + 过期）
│   ├── plugin_system.py           # 插件加载/卸载
│   ├── utils/
│   │   ├── interfaces.py          # AIService/Designer/ExecutorAI 等接口定义
│   │   └── json_fixer.py          # LLM JSON 输出修复
│   ├── models/                    # Pydantic 数据模型
│   ├── logging_config.py
│   │
│   ├── designer/ executor/ dispatcher/ toolmanager/ knowledge/
│   │                              # ⚠ 全部只有空 __init__.py（占位目录，无实现）
│   └── knowledge/、hello_world/ 等  # 实验残留，未接入主链路
│
├── frontend/                      # React 18 + Vite + Tailwind + Zustand（dev 端口 3000）
│   ├── src/components/            # Sidebar / InputView / DesignView / PlanView /
│   │                              # ExecutionView / HistoryView / ToolsView /
│   │                              # ConfigView / AuditView / LogPanel / ToastHub / AiPanel
│   ├── src/api/                   # 后端客户端
│   ├── src/stores/                # Zustand 状态（window.__useAppStore 调试钩子）
│   └── src/types/  src/hooks/  src/test/
│
├── rust/                          # Rust crates：common / db / dispatcher / logger /
│                                  # server / tool_manager / gui（与 Python 主链路未打通）
├── tests/                         # pytest 测试；从仓库根目录运行 pytest
│   ├── python/                    # 单元测试（数据库/设计器/安全/状态机…22 个文件）
│   ├── integration/               # 集成/端到端测试
│   └── rust/                      # Rust 侧测试
├── scripts/                       # start_api.py（后端启动）、cli.py（CLI 设计助手）、
│                                  # check_env.py、各类 test_*.py 辅助脚本
├── config/                        # models.yaml（提供商与密钥）、api_token、server.yaml
├── data/                          # SQLite 数据库文件
├── docs/                          # 本文档目录；历史报告在 docs/archive/
├── plugins/                       # 插件目录（示例：JsonToolPlugin）
├── schemas/                       # JSON Schema（设计书/计划/工具）
├── *.bat                          # Windows 一键启动脚本（启动完整系统/后端/前端/CLI）
└── 一键启动.bat 等
```

## 关键事实

- **主链路代码集中在两处**：`python/api/server.py`（API + 编排）与
  `python/database.py`（持久化）。`designer/`、`executor/`、`dispatcher/`、
  `toolmanager/`、`knowledge/` 是空壳目录，仅代表设计意图。
- **端口**：后端 8000（`api.server:app`），前端 dev 3000（vite 代理 /api、/ws → 8000）。
  9000（遗留 main.py）与 8090（Rust server，未接线）不在主链路上。
- **工具**：实际实现在 `python/tools/`；根目录 `tools/` 只是规范文档与示例。
- **测试入口**：pytest 必须在仓库根目录运行（`pytest.ini` 的 `testpaths=tests`，
  `tests/conftest.py` 负责把 `python/` 加入 `sys.path`）；在子目录运行会收集不到用例。
