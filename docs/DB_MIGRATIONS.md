# 数据库 Schema 迁移规范（Alembic）

> 自 2026-09-19 起，schema 变更统一走 Alembic 迁移；`database.py::_initialize_tables`
> 保留幂等建表仅作运行时防御，**不再是 schema 演进的入口**。

## 现状

- 框架：Alembic 1.13.1（此前已在 requirements 中，本次启用）
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

已有 `data/eas.db`（表已由运行时创建）的机器执行一次：

```bash
python -m alembic stamp 0001_baseline
```

只写入 `alembic_version` 登记表，不动任何业务数据。本机已于 2026-09-19
完成 stamp（备份 `out/eas.db.bak-20260919`）。

## 第二阶段（后续可选）

- 服务启动时自动 `upgrade head`（替代 `_initialize_tables` 的建表职责）
- `_initialize_tables` 退化为"版本不匹配时拒绝启动"的守卫
- CI 增加 verify_migrations.py 步骤

