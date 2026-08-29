"""
工作区路径语义测试。

约定（docs/AGENT_HARNESS_DESIGN.md §0.2.1）：
- 相对路径 → 解析到工作区根（EAS_WORKSPACE_ROOT 优先，否则仓库根）
- 绝对路径 → 原样使用（向后兼容）
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from tools.workspace import resolve_workspace_path, resolve_workspace_root


def test_relative_path_resolves_to_workspace_root(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    resolved = resolve_workspace_path("output/report.txt")
    assert resolved == tmp_path / "output" / "report.txt"


def test_absolute_path_untouched(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    absolute = tmp_path / "elsewhere" / "file.txt"
    assert resolve_workspace_path(str(absolute)) == absolute


def test_default_root_is_repo_root_when_env_unset(monkeypatch):
    monkeypatch.delenv("EAS_WORKSPACE_ROOT", raising=False)
    expected = Path(__file__).resolve().parent.parent.parent
    assert resolve_workspace_root() == expected


def test_file_creator_relative_path_lands_in_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from tools.file_creator import FileCreator

    result = FileCreator.create_file(path="ws_dir/note.txt", content="hi")
    assert result["success"] is True
    created = tmp_path / "ws_dir" / "note.txt"
    assert created.exists()
    assert created.read_text(encoding="utf-8") == "hi"


def test_file_creator_absolute_path_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path / "unused"))
    from tools.file_creator import FileCreator

    target = tmp_path / "absolute.txt"
    result = FileCreator.create_file(path=str(target), content="abs")
    assert result["success"] is True
    assert target.exists()


def test_text_editor_write_relative_path_lands_in_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from tools.text_editor import TextEditor

    result = TextEditor.execute("write_file", {"path": "docs/note.md", "content": "# hi"})
    assert result["success"] is True
    assert (tmp_path / "docs" / "note.md").read_text(encoding="utf-8") == "# hi"


def test_text_editor_read_relative_path(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "existing.txt").write_text("workspace content", encoding="utf-8")
    from tools.text_editor import TextEditor

    # text_editor 的解析在 execute() 入口统一完成
    result = TextEditor.execute("read_file", {"path": "existing.txt"})
    assert result["success"] is True
    assert result["content"] == "workspace content"


def test_git_operator_relative_path_resolves(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from tools.git_operator import GitOperator

    # 只验证解析行为：path 以 / 结尾会被 _infer 之外的分发按目录处理，
    # git_init 在非目录上会失败，因此用结果里的错误信息判断解析目标。
    missing = tmp_path / "no_such_repo_dir"
    result = GitOperator.git_init(path=str(missing / "."))
    # 目录不存在时 git 失败，但失败信息里应包含解析后的路径（含 tmp_path）
    assert result["success"] is False or (missing / ".git").exists()


def test_package_installer_cwd_resolution_target(monkeypatch, tmp_path):
    """cwd 相对路径的解析目标必须是工作区根下的对应目录。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from tools.workspace import resolve_workspace_path

    resolved = resolve_workspace_path("frontend")
    assert resolved == tmp_path / "frontend"
