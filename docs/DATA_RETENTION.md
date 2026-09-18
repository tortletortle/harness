# 数据保留策略

> P2 可观测性/运维配套。工具：`scripts/prune_data.py`。

## 原则

- **业务记录本体永不自动删除**：plans / designs / steps / artifacts /
  execution_runs / agent_sessions 是交付历史，保留全部。
- **细粒度过程数据按期清理**：事件流水与日志随时间贬值，超期后只留统计意义，
  定期清理防止 SQLite 无限膨胀。
- **运行中的计划绝不触碰**：只清理已终结（Completed / Failed）计划关联的数据。

## 保留期默认值

| 数据 | 表 | 默认保留 | 条件 |
|---|---|---|---|
| 步骤事件流水 | step_events | 60 天 | 且所属计划已终结 |
| 运行日志 | logs | 60 天 | — |
| 审计日志 | audit_logs | 180 天 | 审计要求保留更久 |
| 废弃工作树记录 | worktrees | 30 天 | 仅 status=discarded 的 DB 行 |

文件侧（loguru `logs/eas.log`）已由日志轮转自治：10MB 轮转、保留 5 份、zip 压缩。

## 用法

```bash
python scripts/prune_data.py                # dry-run：只报告将删除的行数
python scripts/prune_data.py --apply        # 备份到 out/ 后真正删除 + VACUUM
python scripts/prune_data.py --events-days 30 --apply   # 自定义保留期
```

安全机制：默认 dry-run；`--apply` 自动先备份 `out/eas.db.bak-prune-<日期>`；
删除在单事务内执行，之后 VACUUM 回收空间。

## 建议节奏

- 单机部署：每月手动跑一次 dry-run 看量，有量再 `--apply`。
- 磁盘吃紧时优先检查 `logs/`（轮转产物）与 `out/`（临时证据文件），
  再考虑缩短 `--events-days`。

