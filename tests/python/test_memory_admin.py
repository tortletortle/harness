# -*- coding: utf-8 -*-
"""scripts/memory_admin.py 运维工具回归：默认 dry-run、整条边界、写入前备份。

工具用于人工整理项目记忆（见 docs/PROJECT_INDEX.md 脚本索引）。这里只跑模块函数，
不启子进程，避免影响真实工作区。
"""

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "scripts"))

import memory_admin  # noqa: E402
from tools import project_memory as pm  # noqa: E402


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    for key in (pm.ENV_MAX_FILE_CHARS, pm.ENV_MAX_FILE_ENTRIES,
                pm.ENV_MAX_ARCHIVE_CHARS, pm.ENV_MAX_ARCHIVE_ENTRIES):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _entry(ts: str, pid: str, status: str = "Completed", steps: int = 2) -> str:
    body = [f"计划 {pid} 执行结束（最终状态: {status}）"]
    body += [f"- 步骤{i} [file_creator] Success —— 步骤{i}" for i in range(1, steps + 1)]
    return f"\n## [{ts}] 执行摘要（计划 {pid}）\n" + "\n".join(body) + "\n"


def _write(ws: Path, target: str, entries) -> Path:
    path = ws / (pm.MEMORY_FILENAME if target == "memory" else pm.ARCHIVE_FILENAME)
    path.write_text("".join(entries), encoding="utf-8")
    return path


def _entries_on(ws: Path, target: str):
    path = ws / (pm.MEMORY_FILENAME if target == "memory" else pm.ARCHIVE_FILENAME)
    if not path.exists():
        return []
    return pm._split_entries(path.read_text(encoding="utf-8"))[1]


def _assert_whole(entries, steps=2):
    for block in entries:
        assert block.startswith("## ["), "出现半截条目"
        assert block.count("- 步骤") == steps


# ===== stats =====

def test_stats_on_empty_workspace(ws, capsys):
    assert memory_admin.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "文件不存在" in out
    assert str(ws) in out


def test_stats_json_reports_counts_and_limits(ws, capsys):
    _write(ws, "memory", [_entry("2026-09-19 10:00:00", f"m{i}") for i in range(3)])
    _write(ws, "archive", [_entry("2026-09-18 10:00:00", f"a{i}") for i in range(5)])
    assert memory_admin.main(["stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    files = {row["target"]: row for row in payload["files"]}
    assert files["memory"]["entries"] == 3
    assert files["archive"]["entries"] == 5
    assert files["memory"]["oldest"].startswith("2026-09-19")
    assert payload["limits"]["file_entries"] == pm._MAX_FILE_ENTRIES
    assert payload["workspace_root"] == str(ws)


def test_stats_detects_duplicates_and_half_entries(ws, capsys):
    """重复条目与历史遗留的半截记录都要被指出来。"""
    body = _entry("2026-09-19 10:00:00", "dup1")
    _write(ws, "memory", ["or] Success —— 步骤2\n", body, body])
    assert memory_admin.main(["stats", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)["files"][0]
    assert row["entries"] == 2
    assert row["duplicates"] == 1
    assert row["half_entries"] == 1


def test_stats_accepts_human_date_only_headers(ws, capsys):
    """人工索引条目用 `## [YYYY-MM-DD]`，不能被误报为异常。"""
    _write(ws, "memory", [
        "\n## [2026-09-13] 结论：检查点依赖恢复已完成\n- 修复了若干问题\n",
        _entry("2026-09-19 10:00:00", "auto1"),
    ])
    assert memory_admin.main(["stats", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)["files"][0]
    assert row["entries"] == 2
    assert row["half_entries"] == 0
    assert row["oldest"] == "2026-09-13"
    assert row["newest"].startswith("2026-09-19 10:00")


# ===== keep =====

def test_keep_archive_dry_run_does_not_write(ws, capsys):
    items = [_entry(f"2026-09-{i + 1:02d} 10:00:00", f"k{i}") for i in range(10)]
    path = _write(ws, "archive", items)
    before = path.read_text(encoding="utf-8")
    assert memory_admin.main(["keep", "--entries", "3", "--target", "archive"]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before, "dry-run 不得写文件"


def test_keep_archive_apply_keeps_newest_whole_entries(ws, capsys):
    items = [_entry(f"2026-09-{i + 1:02d} 10:00:00", f"k{i}") for i in range(10)]
    _write(ws, "archive", items)
    assert memory_admin.main(["keep", "--entries", "3", "--target", "archive", "--apply"]) == 0
    out = capsys.readouterr().out
    kept = _entries_on(ws, "archive")
    assert len(kept) == 3
    _assert_whole(kept)
    assert "计划 k9 " in kept[-1]
    # 保留最新 3 条 = k7 / k8 / k9
    assert "计划 k7 " in kept[0]
    assert "计划 k6 " not in "".join(kept)
    assert "k0" not in "".join(kept)
    assert "备份:" in out
    backups = list((ws / "out").glob("memory-admin-backup-*"))
    assert len(backups) == 1, "写入前必须备份"


def test_keep_archive_noop_when_under_limit(ws, capsys):
    _write(ws, "archive", [_entry("2026-09-19 10:00:00", "only")])
    assert memory_admin.main(["keep", "--entries", "10", "--target", "archive", "--apply"]) == 0
    assert "无需裁剪" in capsys.readouterr().out
    assert not (ws / "out").exists(), "没有改动就不该产生备份"


def test_keep_memory_moves_entries_to_archive(ws, capsys):
    """裁剪主文件不能丢数据：被裁条目必须进归档。"""
    items = [_entry(f"2026-09-{i + 1:02d} 10:00:00", f"m{i}") for i in range(6)]
    _write(ws, "memory", items)
    assert memory_admin.main(["keep", "--entries", "2", "--target", "memory", "--apply"]) == 0
    memory_entries = _entries_on(ws, "memory")
    archive_entries = _entries_on(ws, "archive")
    assert len(memory_entries) == 2
    assert len(archive_entries) == 4
    _assert_whole(memory_entries)
    _assert_whole(archive_entries)
    assert "计划 m5 " in "".join(memory_entries)
    assert "计划 m0 " in "".join(archive_entries)


def test_keep_chars_limit_is_boundary_safe(ws):
    items = [_entry(f"2026-09-{i + 1:02d} 10:00:00", f"c{i}") for i in range(8)]
    _write(ws, "memory", items)
    assert memory_admin.main(["keep", "--entries", "99", "--chars", "600",
                              "--target", "memory", "--apply"]) == 0
    text = (ws / pm.MEMORY_FILENAME).read_text(encoding="utf-8")
    assert len(text) <= 600
    _assert_whole(_entries_on(ws, "memory"))


def test_keep_rejects_non_positive_entries(ws, capsys):
    assert memory_admin.main(["keep", "--entries", "0", "--target", "archive"]) == 2
    assert "必须为正整数" in capsys.readouterr().out


# ===== dedupe =====

def test_dedupe_dry_run_then_apply(ws, capsys):
    body_old = _entry("2026-09-19 10:00:00", "same1", status="Failed")
    body_new = _entry("2026-09-19 11:00:00", "same1")
    path = _write(ws, "memory", [body_old, body_new])
    before = path.read_text(encoding="utf-8")
    assert memory_admin.main(["dedupe"]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before

    assert memory_admin.main(["dedupe", "--apply"]) == 0
    text = path.read_text(encoding="utf-8")
    assert text.count("## [") == 1
    assert "最终状态: Completed" in text, "保留最新一条"
    assert "最终状态: Failed" not in text


def test_dedupe_keeps_distinct_entries(ws):
    _write(ws, "memory", [
        _entry("2026-09-19 10:00:00", "p1"),
        _entry("2026-09-19 10:00:01", "p2"),
    ])
    assert memory_admin.main(["dedupe", "--apply"]) == 0
    assert len(_entries_on(ws, "memory")) == 2


# ===== CLI 行为 =====

def test_no_subcommand_prints_help(capsys):
    assert memory_admin.main([]) == 1
    out = capsys.readouterr().out.lower()
    assert "usage" in out and "stats" in out


def test_workspace_flag_overrides_env(ws, tmp_path, capsys):
    other = tmp_path / "other_ws"
    other.mkdir()
    _write(other, "memory", [_entry("2026-09-19 10:00:00", "alt")])
    assert memory_admin.main(["--workspace", str(other), "stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_root"] == str(other)
    assert payload["files"][0]["entries"] == 1

# ===== 自检报告 =====

def test_stats_report_dry_run_does_not_write(ws, capsys):
    _write(ws, "memory", [_entry("2026-09-19 10:00:00", "r1")])
    assert memory_admin.main(["stats", "--report"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "memory-stats-" in out
    assert "项目记忆自检报告" in out, "dry-run 也要把报告内容打出来供审阅"
    assert not (ws / "data" / "reports").exists(), "dry-run 不得创建目录或文件"


def test_stats_report_apply_writes_markdown(ws, capsys):
    _write(ws, "memory", [_entry("2026-09-19 10:00:00", "r1"),
                          _entry("2026-09-19 11:00:00", "r2")])
    _write(ws, "archive", [_entry("2026-09-18 10:00:00", "a1")])
    assert memory_admin.main(["stats", "--report", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "[report] 已写入" in out

    saved = list((ws / "data" / "reports").glob("memory-stats-*.md"))
    assert len(saved) == 1
    assert re.fullmatch(r"memory-stats-\d{8}-\d{4}\.md", saved[0].name), saved[0].name

    text = saved[0].read_text(encoding="utf-8")
    assert text.startswith("# 项目记忆自检报告")
    assert str(ws) in text
    assert f"{pm._MAX_FILE_ENTRIES} 条" in text, "报告要带容量上限，便于判断余量"
    assert "| `memory` | 2 |" in text
    assert "| `archive` | 1 |" in text
    assert "未发现重复或异常条目" in text


def test_stats_report_flags_duplicates(ws, capsys):
    body = _entry("2026-09-19 10:00:00", "dup9")
    _write(ws, "memory", [body, body])
    assert memory_admin.main(["stats", "--report", "--apply"]) == 0
    saved = next((ws / "data" / "reports").glob("memory-stats-*.md"))
    text = saved.read_text(encoding="utf-8")
    assert "存在重复或异常条目" in text
    assert "dedupe" in text
