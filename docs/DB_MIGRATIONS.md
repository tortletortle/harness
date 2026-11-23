# 数据库 Schema 迁移规范（Alembic）

> 自 2026-09-19 起，schema 变更统一走 Alembic 迁移；`database.py::_initialize_tables`
> 保留幂等建表仅作运行时防御，**不再是 schema 演进的入口**。

## 现状

- 当前迁移链：`0001_baseline` → `0002_run_graph_snapshot`。
- `0002_run_graph_snapshot` 为 `execution_runs` 增加不可变 WorkGraph 快照 JSON 与 SHA-256 指纹；旧 Run 保持 NULL，不根据当前计划状态回填历史图。
- 新 Run 在认领事务中冻结活动分支的步骤 ID、顺序、工具标识、依赖和父步骤别名；单次快照最多 256 步，超过上限会明确标记不完整。
- 框架：Alembic 1.13.1（此前已在 requirements 中启用）
- 配置：`alembic.ini`（**必须保持纯 ASCII**——alembic 用系统 locale 读取，
  zh-CN Windows 下是 GBK，含 UTF-8 中文会 UnicodeDecodeError）
- 脚本目录：`python/migrations/`（env.py + versions/）
- 数据库路径：默认 `data/eas.db`，环境变量 `EAS_DB_PATH` 优先（与运行时一致）
- baseline：`0001_baseline` 固化 12 表 + 10 索引（核对源 `out/schema_baseline.sql`，
  由 `scripts/dump_schema.py` 生成）

## 常用命令（仓库根目录）

```bash
python -m alembic current              # 查看当前版本
python -m alembic upgrade head         # 升级到最新
python -m alembic history --verbose    # 查看迁移历史
python -m alembic revision -m "描述"   # 生成空迁移骨架（手写 DDL）
```

## 规则

1. **改 schema = 写迁移**。任何表/列/索引变更先写 `versions/` 下的迁移文件，
   再同步 `database.py` 的建表 SQL（保持新库两条路径产物一致）。
2. **手写 DDL，不用 autogenerate**。运行时是手写 sqlite3，没有 SQLAlchemy
   模型可供对比，autogenerate 结果不可信。
3. **SQLite ALTER 限制**：只支持 ADD COLUMN / RENAME。改列类型、加约束、
   删列必须用 `op.batch_alter_table`（新表-拷贝-换名），迁移前先备份。
4. **幂等防御**：DDL 一律带 `IF NOT EXISTS` / 列存在性检测，与运行时
   `_initialize_tables` 并存期间双方互不破坏。
5. **一致性验证**：改动后必跑 `python scripts/verify_migrations.py` ——
   它同时验证 ①新库 upgrade head 可用 ②迁移产物与运行时 schema 逐对象一致
   ③既有库 stamp 流程。三项必须全绿（`SCHEMA_IDENTICAL`）。
6. **生产库操作前备份**：`Copy-Item data/eas.db out/eas.db.bak-<日期>`。
7. baseline 不支持 downgrade（回退即删库，人工决策）。后续迁移应尽量
   提供可用的 downgrade。

## 既有部署如何接入

已有 `data/eas.db`（表已由运行时创建）的机器：
- 如果尚未登记 Alembic 版本，先备份数据库，再执行 `python -m alembic stamp 0001_baseline`。
- 已登记 `0001_baseline` 的机器执行 `python -m alembic upgrade head`，应用后续迁移。

`stamp` 只写入 `alembic_version`，不执行 DDL；`upgrade head` 才会应用 `0002_run_graph_snapshot`。本机此前已于 2026-09-19
完成 baseline stamp（备份 `out/eas.db.bak-20260919`）。

## 第二阶段（后续可选）

- 服务启动时自动 `upgrade head`（替代 `_initialize_tables` 的建表职责）
- `_initialize_tables` 退化为"版本不匹配时拒绝启动"的守卫
- CI 增加 verify_migrations.py 步骤



## PR #7：Run-scoped Evidence Snapshot

- `execution_runs.graph_snapshot_json` 保存 Run 启动时的活动 WorkGraph 结构；`graph_snapshot_hash` 用 SHA-256 校验快照完整性。
- 新 Run 的图快照与 Run 创建处于同一 SQLite 写事务，后续重规划、克隆或步骤修改不会改变历史 Run 的图定义。
- `Database.get_failure_attribution_snapshot(run_id)` 在一个只读事务中读取 Run、快照图、Attempts、Run 级 Artifacts 和 Agent Sessions/Turns。
- `step_events` 与 `audit_logs` 当前仍没有 Run 外键。PR #7 只统计其计划/分支级未关联记录，不根据时间或 step_id 猜测所属 Run，也不把它们送入归因。
- 快照对所有集合使用有界读取与 `LIMIT + 1` 截断检测；原始 Attempt outputs 仅供本地结构化分析器使用，不得直接发送给模型。
- 旧 Run 没有图快照时返回 `missing_legacy_snapshot`，不从可变的当前 steps 表伪造历史图。
