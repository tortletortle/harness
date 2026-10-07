# EAS 2.0 开发指南

## 环境要求

- Python 3.10+（依赖：`pip install -r python/requirements.txt`）
- Node.js 18+（前端；`cd frontend && npm install`）
- Rust toolchain（仅当改 `rust/` 时需要；主链路不依赖 Rust）

## 启动开发环境

```bash
# 终端 1：后端（端口 8000）
python scripts/start_api.py
# 或手动：
#   PYTHONPATH=<仓库>/python python -m uvicorn api.server:app --host 127.0.0.1 --port 8000

# 终端 2：前端（端口 3000，/api 与 /ws 自动代理到 8000）
cd frontend && npm run dev
```

打开 http://localhost:3000。首次启动后端会自动生成 `config/api_token`。

> 遗留入口 `python/api/main.py`（9000 端口）与 `docs/archive/startup/` 里的旧 bat
>（一键启动/启动选择/scripts/start.bat 等）不在主链路上，排查时不要混淆。
>
> Windows 日常入口：根目录三个 bat，或本页上面的两条命令。

## 测试（改代码后必须跑）

```bash
# 后端 pytest —— 必须在仓库根目录运行！
#   在子目录（如 frontend/、python/）运行会静默收集 0 个用例。
python -m pytest

# 单跑某个文件/用例
python -m pytest tests/python/test_database_errors.py -q

# 前端 vitest（在 frontend/ 下）
npm test
npm run build        # tsc + vite，类型错误会在这里暴露

# Rust
cargo test --workspace
```

说明：

- `pytest.ini` 配置了 `--cov=python`，每次运行输出覆盖率并写 `htmlcov/`。
- LLM 相关测试是离线的：用 `llm_client` 里的 `DeterministicProvider`，不需要真实密钥。
- 涉及网络的测试使用了确定性桩，不要在测试里直连外部服务。

## 代码修改须知

- **错误路径**：先读 [ERROR_HANDLING.md](ERROR_HANDLING.md)。核心契约：
  数据库意外故障抛 `DatabaseError`（False/None 只表示"没找到"）；
  生成失败抛 `GenerationError`；执行器所有状态写库走 `_require_write`；
  计划状态迁移走原子 `claim_plan_status`；前端禁止只 `console.error`。
- **状态机**：计划状态迁移规则见 [state-machine-design.md](state-machine-design.md)，
  改暂停/恢复/回滚逻辑前必读。
- **前端约束**：tsconfig 开启 `verbatimModuleSyntax` 与 `noUnusedLocals`，
  import 用相对路径（无路径别名）；调试时可在控制台用 `window.__useAppStore` 直接检查 store。
- **鉴权**：新增端点一律挂 `dependencies=[Depends(require_token)]`，
  WebSocket 用查询参数 `?token=` 校验（见 `api/auth.py`）。

## CI

`.github/workflows/ci.yml` 三个任务并行：Rust（cargo test）、后端（pytest）、
前端（vitest + build）。提交前本地把三样都跑绿再推。

## 目录速查

见 [STRUCTURE.md](STRUCTURE.md)（含"哪些目录是真代码、哪些是空壳"的说明）。
