# -*- coding: utf-8 -*-
"""daily-standup 技能脚本：汇总最近执行记录 + 工作记忆 → Markdown 日报。

协议：stdin 收 {"instruction", "inputs"} JSON；stdout 出 {"success": ...} JSON。
环境变量 EAS_REPO_ROOT 可指定项目根；缺省按脚本位置向上推导（skills/<id>/scripts → 仓库根）。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO = Path(os.environ.get("EAS_REPO_ROOT") or _HERE.parents[3])


def _fmt_dt(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso):%m-%d %H:%M}"
    except Exception:
        return str(iso)[:16]


def build_report(days: int) -> dict:
    lines = [f"# EAS 每日进度报告 · 生成于 {datetime.now():%Y-%m-%d %H:%M}", ""]
    stats: dict = {}
    db = REPO / "data" / "eas.db"
    if not db.exists():
        lines += ["（未找到 data/eas.db，跳过执行统计）"]
    else:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT status, COUNT(*) FROM plans WHERE created_at >= ? GROUP BY status",
                (since,),
            ).fetchall()
            stats["plans"] = {k: v for k, v in rows}
            lines += [f"## 近 {days} 天计划状态", ""]
            if rows:
                lines += [f"- {k}: {v}" for k, v in rows]
            else:
                lines += ["- （该时段没有新建计划）"]

            rows2 = con.execute(
                "SELECT s.tool, s.status, COUNT(*) FROM steps s "
                "JOIN plans p ON s.plan_id = p.plan_id "
                "WHERE p.created_at >= ? GROUP BY s.tool, s.status "
                "ORDER BY 3 DESC LIMIT 15",
                (since,),
            ).fetchall()
            lines += ["", "## 步骤执行分布（工具 × 状态）", ""]
            if rows2:
                lines += [f"- {tool} / {st}: {n}" for tool, st, n in rows2]
            else:
                lines += ["- （无步骤执行记录）"]

            fails = con.execute(
                "SELECT p.plan_id, s.step_order, s.tool, s.failure_reason "
                "FROM steps s JOIN plans p ON s.plan_id = p.plan_id "
                "WHERE s.status = 'Failed' AND s.is_obsolete = 0 AND p.created_at >= ? "
                "ORDER BY p.created_at DESC LIMIT 5",
                (since,),
            ).fetchall()
            if fails:
                lines += ["", "## 失败步骤（如实记录）", ""]
                for pid, order, tool, reason in fails:
                    lines.append(f"- {pid[:8]} #{order} [{tool}]: {(reason or 'unknown')[:140]}")

            latest = con.execute(
                "SELECT plan_id, status, created_at FROM plans "
                "ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            if latest:
                lines += ["", "## 最近计划", ""]
                for pid, st, ts in latest:
                    lines.append(f"- {pid[:8]} · {st} · {_fmt_dt(ts)}")
        finally:
            con.close()

    mem = REPO / "PROJECT_MEMORY.md"
    if mem.exists():
        lines += ["", "## 工作记忆摘录", "", mem.read_text(encoding="utf-8")[:1500]]

    return {"report": "\n".join(lines), "stats": stats}


def main() -> None:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception:
        req = {}
    inputs = req.get("inputs") or {}
    try:
        days = max(1, int(inputs.get("days") or 1))
    except (TypeError, ValueError):
        days = 1

    result = build_report(days)
    out_dir = REPO / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = out_dir / f"standup-{datetime.now():%Y%m%d-%H%M}.md"
    saved.write_text(result["report"], encoding="utf-8")

    json.dump(
        {"success": True, "report": result["report"], "stats": result["stats"], "saved_to": str(saved)},
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
