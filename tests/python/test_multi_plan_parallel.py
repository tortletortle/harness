# -*- coding: utf-8 -*-
"""多计划并行执行回归：绑定 worktree 的计划不占主工作区执行权，可与其他计划并行。

并发安全前提：
- 同计划重复执行由 DB 状态机原子认领（start_execution_run 条件 UPDATE）拒绝；
- 工作区隔离由 contextvar（asyncio 任务上下文）+ 工具线程池显式传播保证。
"""
import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path
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
from tools.git_operator import GitOperator

GIT_IDENTITY = ["-c", "user.name=EAS Test", "-c", "user.email=eas-test@local"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args], cwd=str(repo),
        capture_output=True, text=True, timeout=60,
    )


def _make_plan(db: Database, description: str, out_name: str) -> "ExecutionPlan":
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request=description, timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    ))
    step = Step(step_id=uuid4(), order=1, description=description, tool="file_creator",
                inputs={"path": f"out/{out_name}.txt", "content": description},
                check_criteria="手动检查")
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.PENDING,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[step])],
        implementation_list=[],
    )
    db.save_plan(plan)
    return plan


def _bind_worktree(repo: Path, db: Database, plan_id: str, name: str) -> dict:
    added = GitOperator.execute("worktree_add", {"path": str(repo), "name": name})
    assert added["success"], added
    db.create_worktree_record(
        worktree_id=f"wt-{name}", plan_id=plan_id, repo_path=str(repo),
        worktree_path=added["worktree_path"], branch=added["branch"],
    )
    return added


@pytest.mark.asyncio
async def test_two_worktree_plans_run_in_parallel(tmp_path, monkeypatch):
    """两个各绑 worktree 的计划并发执行：互不干扰、同时完成、各自产物落在各自 worktree。"""
    from api import server as server_mod
    from executor import DefaultExecutor

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    server_mod.state.executor = DefaultExecutor(llm_client=None, model_config=[])
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(repo))

    plan_a = _make_plan(db, "任务A", "a-product")
    plan_b = _make_plan(db, "任务B", "b-product")
    wt_a = _bind_worktree(repo, db, str(plan_a.plan_id), "par-a")
    wt_b = _bind_worktree(repo, db, str(plan_b.plan_id), "par-b")

    started: list[str] = []

    async def real_tool_step(plan, row):
        """真实 file_creator 执行（完整覆盖 contextvar→to_thread→工具线程池传播链）。"""
        import json as _json

        from tools.file_creator import FileCreator

        started.append(str(row["step_id"]))
        await asyncio.sleep(0.2)
        inputs = _json.loads(row["inputs"]) if isinstance(row.get("inputs"), str) else (row.get("inputs") or {})
        result = await asyncio.to_thread(
            lambda: FileCreator.execute("create_file", {"path": inputs["path"], "content": inputs.get("content", "")})
        )
        assert result["success"], result
        return {"success": True, "check_status": "unverified", "output": result, "error": None, "blocked": False, "retryable": False}

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", real_tool_step)

    async def run_plan(plan):
        await server_mod.execute_plan_background(db.get_plan(str(plan.plan_id)))

    await asyncio.gather(
        run_plan(plan_a),
        run_plan(plan_b),
    )

    assert db.get_plan_status(str(plan_a.plan_id)) == "Completed"
    assert db.get_plan_status(str(plan_b.plan_id)) == "Completed"
    assert (Path(wt_a["worktree_path"]) / "out" / "a-product.txt").exists()
    assert (Path(wt_b["worktree_path"]) / "out" / "b-product.txt").exists()
    assert not (repo / "out" / "a-product.txt").exists()
    assert not (repo / "out" / "b-product.txt").exists()


@pytest.mark.asyncio
async def test_worktree_plan_parallel_with_main_workspace_plan(tmp_path, monkeypatch):
    """worktree 计划与主工作区计划同时执行：主工作区产物不落入 worktree。"""
    from api import server as server_mod
    from executor import DefaultExecutor

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    server_mod.state.executor = DefaultExecutor(llm_client=None, model_config=[])
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(repo))

    plan_wt = _make_plan(db, "隔离任务", "wt-product")
    plan_main = _make_plan(db, "主工作区任务", "main-product")
    wt = _bind_worktree(repo, db, str(plan_wt.plan_id), "mix-wt")

    async def real_tool_step(plan, row):
        """真实 file_creator 执行，验证并行时工作区按 contextvar 隔离。"""
        import json as _json

        from tools.file_creator import FileCreator

        await asyncio.sleep(0.25)
        inputs = _json.loads(row["inputs"]) if isinstance(row.get("inputs"), str) else (row.get("inputs") or {})
        result = await asyncio.to_thread(
            lambda: FileCreator.execute("create_file", {"path": inputs["path"], "content": inputs.get("content", "")})
        )
        assert result["success"], result
        return {"success": True, "check_status": "unverified", "output": result, "error": None, "blocked": False, "retryable": False}

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", real_tool_step)

    await asyncio.gather(
        server_mod.execute_plan_background(db.get_plan(str(plan_wt.plan_id))),
        server_mod.execute_plan_background(db.get_plan(str(plan_main.plan_id))),
    )

    assert db.get_plan_status(str(plan_wt.plan_id)) == "Completed"
    assert db.get_plan_status(str(plan_main.plan_id)) == "Completed"
    assert (Path(wt["worktree_path"]) / "out" / "wt-product.txt").exists()
    assert (repo / "out" / "main-product.txt").exists()
    assert not (repo / "out" / "wt-product.txt").exists()
    assert not (Path(wt["worktree_path"]) / "out" / "main-product.txt").exists()
