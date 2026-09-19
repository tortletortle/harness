# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)；条目格式参考 Keep a Changelog。
0.x 阶段：MINOR 表示功能里程碑，PATCH 表示修复/小改进。

## [Unreleased]

### Added
- Python 依赖锁定：python/uv.lock（uv lock，74 packages），pyproject 补 playwright
- Correlation ID 贯穿：HTTP middleware（X-Correlation-ID 生成/透传/回传）+
  后台执行链路绑定 run-{run_id} + loguru patcher 自动注入日志 trace= 字段
- 数据保留策略：scripts/prune_data.py（默认 dry-run，--apply 自动备份）+
  docs/DATA_RETENTION.md

### Changed
- python/pyproject.toml version 对齐 0.2.0（并入版本单一来源清单）

### Fixed
- 数据库路径解析统一为 python/paths.resolve_db_path（EAS_DB_PATH 优先，否则仓库根
  data/eas.db，基于 __file__ 不依赖启动目录）：api/server.py 不再用 cwd 相对路径 +
  ../data 回退（从 python/ 启动会产生第二份 python/data/eas.db），Alembic env.py 与
  运行时共用同一解析器，迁移的库与服务实际使用的库保证一致；新增 12 项回归
- 离线回归命令开箱可用：test_desktop_approval / test_desktop_artifacts 在未配置
  EAS_TOOLS_DIR（外部 harness_tools 仓库）时整文件 skip，不再在收集阶段 KeyError
  中断整个 pytest 会话

## [0.2.0] - 2026-09-19

首个正式打 tag 的版本，将此前全部工作固化为基线。

### Added
- 任务编排核心链路：Designer 设计书生成/审批 → 执行计划 → Executor 分步执行，
  SQLite 持久化 + React 控制台（新建/设计/计划/执行/工具/历史/审计/配置视图）
- WorkGraph：显式依赖 DAG、资源冲突检测、有界并发、多计划并行（worktree 隔离）
- 检查点恢复、动态重规划、AgentSession/Turn 记录、Artifact 内容层、交付报告
- 任务模式识别（quick-task 直达工具模式）
- 15 个内置工具 + Skills/MCP/coding_agent/chat/IM 通知生态层
- 模型配置工作台：多 Provider、主备模型、上游探测、DPAPI 密钥加密保存
- Rust 产品化：eas-server（契约校验/工具伴随，Bearer 认证）+ eas-gui
  桌面控制台（egui 0.36，Workbench 布局，暗色双主题，命令面板）
- Alembic 迁移框架 + baseline schema（docs/DB_MIGRATIONS.md）
- 版本管理规范：单一版本源、git 溯源注入、CHANGELOG（docs/VERSIONING.md）

### Security
- 全端点 token 认证（常数时间比较）、SSRF 防护（含重定向链复检）、
  参数绑定 SQL、fail-closed 校验、密钥仅环境变量/DPAPI 加密存储

### 验证基线
- Python 离线回归 1120 passed；前端 vitest 87 passed；Rust workspace 37 passed
- clippy -D warnings 全绿；真实 LLM A/B/C 三级验收通过

## [0.1.0] - 2026-09-10（回溯）

- 核心链路首次完整可用（设计书 → 审批 → 计划 → 执行 → 审计）
- 当时基线：Python 635 passed / 前端 36 passed

