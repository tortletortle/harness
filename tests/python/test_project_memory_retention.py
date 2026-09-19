# -*- coding: utf-8 -*-
"""project_memory 保留策略回归：条数上限 + 环境变量覆盖（2026-09-19）。

背景：文件原来只有 20000 字符硬上限，写满后"写一条丢一条"。现在条数与字符数
双限，且可用环境变量在运行期调整（`EAS_PROJECT_MEMORY_MAX_ENTRIES=20` 即只保留
最近 20 条），滚动仍只在整条边界发生。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from tools import project_memory  # noqa: E402

_ENV_KEYS = (
    project_memory.ENV_MAX_FILE_CHARS,
    project_memory.ENV_MAX_FILE_ENTRIES,
    project_memory.ENV_MAX_ARCHIVE_CHARS,
    project_memory.ENV_MAX_ARCHIVE_ENTRIES,
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """自己的临时工作区 + 干净的环境变量基线。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _entry(pid: str, steps: int = 1) -> str:
    body = [f"计划 {pid} 执行结束（最终状态: Completed）"]
    body += [f"- 步骤{i} [file_creator] Success —— 步骤{i}" for i in range(1, steps + 1)]
    return "\n".join(body)


def _memory_entries(sandbox: Path):
    text = (sandbox / project_memory.MEMORY_FILENAME).read_text(encoding="utf-8")
    return text, project_memory._split_entries(text)[1]


def _archive_entries(sandbox: Path):
    path = sandbox / project_memory.ARCHIVE_FILENAME
    if not path.exists():
        return []
    return project_memory._split_entries(path.read_text(encoding="utf-8"))[1]


def test_entry_limit_bounds_file(sandbox, monkeypatch):
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "5")
    for i in range(20):
        result = project_memory.save_entry("执行摘要", _entry(f"p{i:03d}"))
    text, entries = _memory_entries(sandbox)
    assert len(entries) == 5, "条数上限未生效"
    assert result["retained"] == 5
    assert "计划 p019 执行结束" in text, "最新一条必须在文件里"
    # 保留的是最新 5 条（p015~p019），再早的整条滚出
    assert "计划 p015 执行结束" in text
    assert "计划 p014 执行结束" not in text
    assert "计划 p000 执行结束" not in text
    archived = _archive_entries(sandbox)
    assert len(archived) == 15, "滚出的整条必须进归档"
    assert "计划 p000 执行结束" in archived[0]


def test_char_limit_still_applies(sandbox, monkeypatch):
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_CHARS, "1200")
    for i in range(30):
        project_memory.save_entry("执行摘要", _entry(f"c{i:03d}"))
    text, entries = _memory_entries(sandbox)
    assert len(text) <= 1200
    assert entries, "字符上限生效时也必须保留最新条目"


def test_archive_entry_limit(sandbox, monkeypatch):
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "2")
    monkeypatch.setenv(project_memory.ENV_MAX_ARCHIVE_ENTRIES, "3")
    for i in range(12):
        project_memory.save_entry("执行摘要", _entry(f"a{i:03d}"))
    archived = _archive_entries(sandbox)
    assert len(archived) == 3, "归档条数上限未生效"
    # 归档保留的是最新的 3 条
    assert "计划 a009 执行结束" in archived[2][:120] or "计划 a009" in "".join(archived)
    assert "计划 a000" not in "".join(archived)


def test_env_overrides_module_constant(sandbox, monkeypatch):
    """环境变量优先于模块常量（运维可覆盖，不改代码）。"""
    path = sandbox / project_memory.MEMORY_FILENAME
    monkeypatch.setattr(project_memory, "_MAX_FILE_ENTRIES", 3)
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "7")
    for i in range(10):
        project_memory.save_entry("执行摘要", _entry(f"e{i:03d}"))
    _, entries = _memory_entries(sandbox)
    assert len(entries) == 7


def test_invalid_env_falls_back_to_default(sandbox, monkeypatch):
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "abc")
    for i in range(8):
        project_memory.save_entry("执行摘要", _entry(f"i{i:03d}"))
    _, entries = _memory_entries(sandbox)
    assert len(entries) == 8, "非法值应回落默认（60 条），而不是清空或报错"


def test_non_positive_env_falls_back_to_default(sandbox, monkeypatch):
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "-3")
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_CHARS, "0")
    for i in range(5):
        project_memory.save_entry("执行摘要", _entry(f"n{i:03d}"))
    text, entries = _memory_entries(sandbox)
    assert len(entries) == 5
    assert len(text) <= project_memory._MAX_FILE_CHARS


def test_limits_are_read_at_call_time(sandbox, monkeypatch):
    for i in range(10):
        project_memory.save_entry("执行摘要", _entry(f"t{i:03d}"))
    assert len(_memory_entries(sandbox)[1]) == 10
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "3")
    project_memory.save_entry("执行摘要", _entry("t010"))
    _, entries = _memory_entries(sandbox)
    assert len(entries) == 3, "上限应在下次写入时立即生效"


def test_collapse_still_works_with_entry_limit(sandbox, monkeypatch):
    """折叠与条数上限共存：同一计划反复重跑不占额外条数。"""
    monkeypatch.setenv(project_memory.ENV_MAX_FILE_ENTRIES, "3")
    for status in ("Failed", "Failed", "Completed"):
        project_memory.save_entry(
            "执行摘要",
            f"计划 same0001 执行结束（最终状态: {status}）\n- 步骤1 [x] Success",
            collapse_duplicates=True,
            dedupe_key="计划 same0001",
        )
    text, entries = _memory_entries(sandbox)
    assert len(entries) == 1
    assert "最终状态: Completed" in text
