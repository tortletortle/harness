# -*- coding: utf-8 -*-
"""project_memory 条目语义回归：整条边界滚动、归档限量、重复折叠。

对应 2026-09-19 记忆文件整理时发现的两个问题：
1. 旧实现超限时从文件头部硬切字符，留下半截条目（`or] Success —— 步骤2`）；
2. 同一计划重跑会累积多条摘要，先写的与最终状态互相矛盾。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from tools import project_memory  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把记忆文件固定在自己的临时工作区里（与会话级隔离一致）。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _memory(sandbox: Path) -> str:
    return (sandbox / project_memory.MEMORY_FILENAME).read_text(encoding="utf-8")


def _archive(sandbox: Path) -> str:
    path = sandbox / project_memory.ARCHIVE_FILENAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _plan_entry(pid: str, steps: int = 4, status: str = "Completed") -> str:
    body = [f"计划 {pid} 执行结束（最终状态: {status}）"]
    body += [f"- 步骤{i} [file_creator] Success —— 步骤{i}" for i in range(1, steps + 1)]
    return "\n".join(body)


def _assert_entries_whole(text: str, steps: int) -> None:
    """每条都必须以完整头部开始、步骤行一条不少。"""
    for block in project_memory._split_entries(text)[1]:
        assert block.startswith("## ["), "出现半截条目"
        assert block.count("- 步骤") == steps, "条目内容被截断"


def test_save_appends_single_entry(sandbox):
    result = project_memory.save_entry("执行摘要", _plan_entry("p000", steps=1))
    assert result["success"] is True
    text = _memory(sandbox)
    assert text.count("## [") == 1
    assert "计划 p000 执行结束" in text


def test_rotation_keeps_whole_entries_only(sandbox):
    """超限滚动后：文件里只剩完整条目，最旧的整条进归档。"""
    for i in range(200):
        project_memory.save_entry("执行摘要", _plan_entry(f"p{i:03d}"))

    text = _memory(sandbox)
    assert len(text) <= project_memory._MAX_FILE_CHARS
    entries = project_memory._split_entries(text)[1]
    assert entries, "滚动后不应把条目全部丢光"
    _assert_entries_whole(text, steps=4)

    archived = _archive(sandbox)
    assert archived, "滚出的条目必须进归档文件"
    _assert_entries_whole(archived, steps=4)
    assert "计划 p000 执行结束" in archived and "计划 p000 执行结束" not in text
    assert "计划 p199 执行结束" in text


def test_recall_returns_tail_after_rotation(sandbox):
    for i in range(200):
        project_memory.save_entry("执行摘要", _plan_entry(f"p{i:03d}"))
    got = project_memory.recall(limit_chars=600)
    assert got["success"] is True
    assert "计划 p199 执行结束" in got["content"]


def test_collapse_by_dedupe_key_replaces_same_plan(sandbox):
    """首行含最终状态（Failed→Completed 会变），折叠必须用稳定的 dedupe_key。"""
    first = project_memory.save_entry("执行摘要", _plan_entry("abc12345", steps=1, status="Failed"),
                                      collapse_duplicates=True, dedupe_key="计划 abc12345")
    second = project_memory.save_entry("执行摘要", _plan_entry("abc12345", steps=1),
                                       collapse_duplicates=True, dedupe_key="计划 abc12345")
    assert first.get("replaced") is None
    assert second.get("replaced") is True
    text = _memory(sandbox)
    assert text.count("## [") == 1, "同一计划的摘要应当覆盖而不是追加"
    assert "执行摘要（计划 abc12345）" in text, "键写进标题，人和 AI 都能直接读到"
    assert "最终状态: Completed" in text
    assert "最终状态: Failed" not in text


def test_collapse_by_first_line_when_no_key(sandbox):
    for _ in range(2):
        project_memory.save_entry("执行摘要", "同样的首行\n同样的内容", collapse_duplicates=True)
    assert _memory(sandbox).count("## [") == 1


def test_collapse_keeps_distinct_plans(sandbox):
    for pid in ("aaaa1111", "bbbb2222"):
        project_memory.save_entry("执行摘要", _plan_entry(pid, steps=1),
                                  collapse_duplicates=True, dedupe_key=f"计划 {pid}")
    assert _memory(sandbox).count("## [") == 2


def test_collapse_does_not_touch_other_titles(sandbox):
    project_memory.save_entry("执行摘要", _plan_entry("cccc3333", steps=1),
                              collapse_duplicates=True, dedupe_key="计划 cccc3333")
    project_memory.save_entry("检查点修复", _plan_entry("cccc3333", steps=1),
                              collapse_duplicates=True, dedupe_key="计划 cccc3333")
    assert _memory(sandbox).count("## [") == 2


def test_default_keeps_append_semantics(sandbox):
    """默认仍是追加式：同样内容写两次就是两条（不改变既有契约）。"""
    project_memory.save_entry("进度", "第一条")
    project_memory.save_entry("进度", "第一条")
    assert _memory(sandbox).count("## [") == 2


def test_archive_trim_is_boundary_safe(sandbox, monkeypatch):
    monkeypatch.setattr(project_memory, "_MAX_ARCHIVE_CHARS", 3000)
    for i in range(400):
        project_memory.save_entry("执行摘要", _plan_entry(f"q{i:03d}", steps=1))
    archived = _archive(sandbox)
    assert archived
    assert len(archived) <= 3000
    _assert_entries_whole(archived, steps=1)


def test_missing_content_is_rejected(sandbox):
    assert project_memory.save_entry("标题", "   ")["success"] is False
