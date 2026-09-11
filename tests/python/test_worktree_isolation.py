# -*- coding: utf-8 -*-
"""Worktree 隔离回归：git_operator 指令组、DB 绑定生命周期、计划执行 workspace 重定向。

git 用例操作真实仓库（tmp_path 内 init），提交身份用 -c 内联注入，不依赖全局配置。
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from database import Database
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    ToolAssessment,
)
from tools.git_operator import GitOperator, _worktree_sibling_path


# ===== 真实 git 测试基建 =====

GIT_IDENTITY = ["-c", "user.name=EAS Test", "-c", "user.email=eas-test@local"]


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *GIT_IDENTITY, *args],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} 失败: {result.stderr}")
    return result


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


# ===== git_operator 指令组 =====

def test_worktree_add_list_diff_merge_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    added = GitOperator.execute("worktree_add", {"path": str(repo), "name": "fix-a"})
    assert added["success"], added
    wt = Path(added["worktree_path"])
    assert wt.is_dir()
    assert added["branch"] == "worktree/fix-a"
    assert added["base_commit"]

    # worktree 内修改并提交
    (wt / "feature.txt").write_text("feature", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "add feature")

    listing = GitOperator.execute("worktree_list", {"path": str(repo)})
    assert listing["success"]
    assert any(Path(e["path"]) == wt for e in listing["worktrees"])

    diff = GitOperator.execute("worktree_diff", {"path": str(repo), "worktree_path": str(wt)})
    assert diff["success"]
    assert "feature.txt" in diff["stat"]

    merged = GitOperator.execute("worktree_merge", {
        "path": str(repo), "worktree_path": str(wt), "message": "merge fix-a",
    })
    assert merged["success"], merged
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature"

    removed = GitOperator.execute("worktree_remove", {"path": str(repo), "name": "fix-a"})
    assert removed["success"]
    assert not wt.exists()


def test_worktree_add_rejects_path_traversal_name(tmp_path):
    repo = _make_repo(tmp_path)
    for bad in ("../escape", "a/b", "..", "."):
        result = GitOperator.execute("worktree_add", {"path": str(repo), "name": bad})
        assert result["success"] is False
        assert result.get("error") == "invalid_name"


def test_worktree_merge_conflict_returns_structured_failure(tmp_path):
    repo = _make_repo(tmp_path)
    added = GitOperator.execute("worktree_add", {"path": str(repo), "name": "fix-b"})
    wt = Path(added["worktree_path"])

    # worktree 分支改 base.txt
    (wt / "base.txt").write_text("from worktree", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "wt change")

    # 主仓库对同一文件做出冲突修改
    (repo / "base.txt").write_text("from main", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main change")

    result = GitOperator.execute("worktree_merge", {"path": str(repo), "worktree_path": str(wt)})
    assert result["success"] is False
    assert result.get("conflict") is True

    # 清理冲突状态，避免污染同目录其他用例
    _git(repo, "merge", "--abort", check=False)


def test_worktree_sibling_path_convention(tmp_path):
    assert _worktree_sibling_path(tmp_path / "repo", "demo") == tmp_path / "repo-wt-demo"


# ===== DB 绑定生命周期 =====

def _save_plan(db: Database) -> str:
    """建最小计划记录（满足 worktrees.plan_id 外键）。"""
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="wt", timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    ))
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.PENDING,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[])],
        implementation_list=[],
    )
    db.save_plan(plan)
    return str(plan.plan_id)


def test_worktree_record_lifecycle(tmp_path):
    db = Database(str(tmp_path / "wt.db"))
    plan_id = _save_plan(db)
    db.create_worktree_record(
        worktree_id="wt-1", plan_id=plan_id, repo_path=str(tmp_path),
        worktree_path=str(tmp_path / "repo-wt-x"), branch="worktree/x",
    )
    active = db.get_active_worktree(plan_id)
    assert active and active["worktree_id"] == "wt-1"
    assert db.close_worktree("wt-1", "bogus") is False
    assert db.close_worktree("wt-1", "merged") is True
    assert db.get_active_worktree(plan_id) is None
    assert db.get_worktree("wt-1")["status"] == "merged"


# ===== 计划执行 workspace 重定向（端到端） =====

class _StubExecutor:
    async def execute_step(self, step):
        return {"success": True, "check_status": "unverified", "output": {}, "error": None, "blocked": False}


@pytest.mark.asyncio
async def test_plan_execution_redirects_workspace_to_worktree(tmp_path, monkeypatch):
    """绑定 worktree 的计划执行时，file_creator 的相对路径落在 worktree 内。"""
    from api import server as server_mod
    from executor import DefaultExecutor

    repo = _make_repo(tmp_path)
    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    server_mod.state.executor = DefaultExecutor(llm_client=None, model_config=[])

    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="wt", timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    ))
    steps = [
        Step(step_id=uuid4(), order=1, description="写文件", tool="file_creator",
             inputs={"path": "out/wt-proof.txt", "content": "hello-wt"}, check_criteria="手动检查"),
    ]
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.PENDING,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=steps)],
        implementation_list=[],
    )
    db.save_plan(plan)

    # 通过同一套工具指令创建并绑定 worktree（API 层之下）
    added = GitOperator.execute("worktree_add", {"path": str(repo), "name": "exec-demo"})
    assert added["success"]
    db.create_worktree_record(
        worktree_id="wt-exec", plan_id=str(plan.plan_id), repo_path=str(repo),
        worktree_path=added["worktree_path"], branch=added["branch"],
    )

    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(repo))
    await server_mod.execute_plan_background(db.get_plan(str(plan.plan_id)))

    assert db.get_plan_status(str(plan.plan_id)) == "Completed"
    # 产物落在 worktree，而非主仓库
    assert (Path(added["worktree_path"]) / "out" / "wt-proof.txt").read_text(encoding="utf-8") == "hello-wt"
    assert not (repo / "out" / "wt-proof.txt").exists()
    # 执行结束后 env 恢复
    assert os.environ.get("EAS_WORKSPACE_ROOT") == str(repo)

    # 合并回主仓库
    merged = GitOperator.execute("worktree_merge", {
        "path": str(repo), "worktree_path": added["worktree_path"],
    })
    assert merged["success"]
    assert (repo / "out" / "wt-proof.txt").read_text(encoding="utf-8") == "hello-wt"
