"""Workspace fingerprints are bounded, content-free observations, not snapshots."""
import subprocess

from domain.workspace_fingerprint import capture_workspace_fingerprint


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Harness Test")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "baseline")
    return root


def test_fingerprint_is_stable_and_does_not_expose_paths_or_contents(tmp_path):
    root = _repo(tmp_path)
    first = capture_workspace_fingerprint(root)
    second = capture_workspace_fingerprint(root)
    assert first["complete"] is True
    assert first["workspace_sha256"] == second["workspace_sha256"]
    assert first["source_revision"]
    assert first["dependency_sha256"]
    assert first["runtime_sha256"]
    assert "app.py" not in str(first)
    assert "value = 1" not in str(first)


def test_tracked_and_untracked_changes_change_fingerprint(tmp_path):
    root = _repo(tmp_path)
    baseline = capture_workspace_fingerprint(root)
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    (root / "notes.txt").write_text("private note\n", encoding="utf-8")
    changed = capture_workspace_fingerprint(root)
    assert changed["complete"] is True
    assert changed["workspace_sha256"] != baseline["workspace_sha256"]
    assert changed["tracked_changed_files"] == 1
    assert changed["untracked_files"] == 1
    assert "private note" not in str(changed)
    assert "notes.txt" not in str(changed)


def test_tracked_deletion_is_a_valid_workspace_change(tmp_path):
    root = _repo(tmp_path)
    baseline = capture_workspace_fingerprint(root)
    (root / "app.py").unlink()
    deleted = capture_workspace_fingerprint(root)
    assert deleted["complete"] is True
    assert deleted["workspace_sha256"] != baseline["workspace_sha256"]
    assert deleted["tracked_changed_files"] == 1


def test_dependency_manifest_changes_dependency_fingerprint(tmp_path):
    root = _repo(tmp_path)
    (root / "requirements.txt").write_text("example==1.0\n", encoding="utf-8")
    first = capture_workspace_fingerprint(root)
    (root / "requirements.txt").write_text("example==2.0\n", encoding="utf-8")
    second = capture_workspace_fingerprint(root)
    assert first["complete"] and second["complete"]
    assert first["dependency_sha256"] != second["dependency_sha256"]


def test_non_git_workspace_fails_closed(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    result = capture_workspace_fingerprint(root)
    assert result["complete"] is False
    assert result["error_code"] == "git_head_unavailable"


def test_oversized_changed_file_marks_fingerprint_incomplete(tmp_path):
    root = _repo(tmp_path)
    (root / "large.bin").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    result = capture_workspace_fingerprint(root)
    assert result["complete"] is False
    assert result["error_code"] == "changed_file_unreadable"
