"""EAS 数据保留清理工具（P2 数据保留策略，见 docs/DATA_RETENTION.md）。

只清理"细粒度过程数据"，业务记录本体（plans / designs / artifacts / steps）永不删除：
- step_events   : 已终结（Completed/Failed）计划、超过保留期的步骤事件
- audit_logs    : 超过保留期的审计日志（默认保留期更长）
- logs          : 超过保留期的运行日志（当前项目未使用该表，顺带覆盖）
- worktrees     : discarded 状态、超过保留期的记录行（仅 DB 行，不动磁盘目录）

用法（任意目录执行均可；数据库路径与运行时同规则：EAS_DB_PATH 优先，否则仓库根 data/eas.db）：
    python scripts/prune_data.py                 # dry-run：只统计将删除多少行
    python scripts/prune_data.py --apply         # 真正执行删除 + VACUUM
    python scripts/prune_data.py --events-days 30 --audit-days 180 --apply

安全设计：
- 默认 dry-run，必须显式 --apply 才写库；
- --apply 前自动备份数据库到仓库根 out/eas.db.bak-prune-<日期>；
- 只按"计划已终结 + 时间早于阈值"双条件删除，运行中计划的数据绝不触碰。
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 与运行时共用数据库路径解析器（python/paths.py），不再要求从项目根目录执行
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "python"))
from paths import resolve_db_path  # noqa: E402

TERMINAL_PLAN_STATUSES = ("Completed", "Failed")


def cutoff_iso(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="EAS 数据保留清理（默认 dry-run）")
    parser.add_argument("--db", default=resolve_db_path(),
                        help="数据库路径（默认同运行时：EAS_DB_PATH，否则仓库根 data/eas.db）")
    parser.add_argument("--events-days", type=int, default=60,
                        help="step_events / logs 保留天数（默认 60）")
    parser.add_argument("--audit-days", type=int, default=180,
                        help="audit_logs 保留天数（默认 180，审计数据保留更久）")
    parser.add_argument("--worktree-days", type=int, default=30,
                        help="discarded worktree 记录保留天数（默认 30）")
    parser.add_argument("--apply", action="store_true", help="真正执行删除（默认只统计）")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}")
        return 1

    ev_cut = cutoff_iso(args.events_days)
    au_cut = cutoff_iso(args.audit_days)
    wt_cut = cutoff_iso(args.worktree_days)

    # (标签, 统计 SQL, 删除 SQL, 参数)
    jobs = [
        ("step_events(terminal plans)",
         "SELECT COUNT(*) FROM step_events WHERE created_at < ? AND plan_id IN "
         "(SELECT plan_id FROM plans WHERE status IN (?, ?))",
         "DELETE FROM step_events WHERE created_at < ? AND plan_id IN "
         "(SELECT plan_id FROM plans WHERE status IN (?, ?))",
         (ev_cut, *TERMINAL_PLAN_STATUSES)),
        ("logs",
         "SELECT COUNT(*) FROM logs WHERE timestamp < ?",
         "DELETE FROM logs WHERE timestamp < ?",
         (ev_cut,)),
        ("audit_logs",
         "SELECT COUNT(*) FROM audit_logs WHERE timestamp < ?",
         "DELETE FROM audit_logs WHERE timestamp < ?",
         (au_cut,)),
        ("worktrees(discarded)",
         "SELECT COUNT(*) FROM worktrees WHERE status = 'discarded' AND created_at < ?",
         "DELETE FROM worktrees WHERE status = 'discarded' AND created_at < ?",
         (wt_cut,)),
    ]

    conn = sqlite3.connect(db_path)
    try:
        total = 0
        plan_rows = []
        for label, count_sql, _, params in jobs:
            n = conn.execute(count_sql, params).fetchone()[0]
            plan_rows.append((label, n))
            total += n

        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] db={db_path} events<{args.events_days}d audit<{args.audit_days}d "
              f"worktree<{args.worktree_days}d")
        for label, n in plan_rows:
            print(f"  {label:<28} {n} rows")
        print(f"  TOTAL {total} rows")

        if not args.apply:
            print("dry-run only; rerun with --apply to delete")
            return 0
        if total == 0:
            print("nothing to delete")
            return 0

        # 备份后执行
        backup = REPO_ROOT / "out" / f"eas.db.bak-prune-{datetime.now():%Y%m%d}"
        backup.parent.mkdir(exist_ok=True)
        conn.close()
        shutil.copy2(db_path, backup)
        print(f"backup -> {backup}")

        conn = sqlite3.connect(db_path)
        with conn:
            for label, _, delete_sql, params in jobs:
                cur = conn.execute(delete_sql, params)
                print(f"  deleted {label:<28} {cur.rowcount} rows")
        conn.execute("VACUUM")
        print("VACUUM done")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

