# -*- coding: utf-8 -*-
"""动态重规划回归：Designer 修复步骤生成（契约 fail-closed）+ replan 端点的
分支克隆 / 依赖改写 / 旧线性计划显式化。"""
import asyncio
import json
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
    StepStatus,
    ToolAssessment,
)
from designer import DefaultDesigner, GenerationError
from llm_client import DeterministicProvider, LLMClient


REPAIR_JSON = """{"steps": [{"description": "补配置文件", "tool": "file_creator",
  "inputs": {"path": "config/needed.yaml", "content": "key: value"},
  "acceptance": [{"type": "file_exists", "path": "config/needed.yaml", "kind": "file"}]}]}"""


def _make_designer(response: str, tmp_path: Path):
    llm = LLMClient()
    llm.register_provider(DeterministicProvider(response))
    config = {"primary": {"provider": "deterministic", "model": "test-model", "temperature": 0.1}}
    return DefaultDesigner(llm, config, None)


def _save_plan_with_failure(db: Database, tmp_path: Path, linear: bool = False):
    """三步计划：1 成功、2 失败（锚点）、3 Pending。linear=True 时不带任何依赖。"""
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="replan 测试", timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    ))
    s1 = Step(step_id=uuid4(), order=1, description="第一步", tool="file_creator",
              inputs={"path": "a.txt", "content": "a"}, check_criteria="手动检查")
    s2 = Step(step_id=uuid4(), order=2, description="失败步骤", tool="command_runner",
              inputs={"command": "exit 1"}, check_criteria="手动检查")
    s3 = Step(step_id=uuid4(), order=3, description="第三步", tool="file_creator",
              inputs={"path": "c.txt", "content": "c"}, check_criteria="手动检查")
    if not linear:
        s2.depends_on = [str(s1.step_id)]
        s3.depends_on = [str(s2.step_id)]
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.FAILED,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[s1, s2, s3])],
        implementation_list=[],
        resume_from_step_id=s2.step_id,
    )
    db.save_plan(plan)
    plan_id = str(plan.plan_id)
    db.update_step(str(s1.step_id), "Success")
    db.update_step_state_machine(str(s2.step_id), "Failed", failure_reason="命令失败")
    return plan_id, [str(s.step_id) for s in (s1, s2, s3)]


# ===== Designer 契约 =====

@pytest.mark.asyncio
async def test_generate_repair_steps_valid(tmp_path):
    designer = _make_designer(REPAIR_JSON, tmp_path)
    steps = await designer.generate_repair_steps(
        failed_step={"description": "跑命令", "tool": "command_runner", "inputs": {"command": "x"}},
        failure_reason="缺少配置文件",
    )
    assert len(steps) == 1
    assert steps[0]["tool"] == "file_creator"
    assert steps[0]["acceptance"]


@pytest.mark.asyncio
async def test_generate_repair_steps_rejects_invalid_contract(tmp_path):
    """修复步骤缺 acceptance / 越权 allowed_tools → GenerationError（fail-closed）。"""
    bad = """{"steps": [{"description": "改代码", "tool": "coding_agent",
      "inputs": {"path": "x.py", "instruction": "改", "allowed_tools": ["command_runner"]}}]}"""
    designer = _make_designer(bad, tmp_path)
    with pytest.raises(GenerationError):
        await designer.generate_repair_steps(
            failed_step={"description": "x", "tool": "command_runner", "inputs": {}},
            failure_reason="y",
        )


# ===== replan 端点核心 =====

@pytest.mark.asyncio
async def test_replan_creates_repair_chain_on_new_branch(tmp_path, monkeypatch):
    """显式依赖计划 replan：新分支含修复步骤链；F 克隆依赖追加 Rn；计划转 Pending。"""
    from api import server as server_mod

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    plan_id, step_ids = _save_plan_with_failure(db, tmp_path)
    _, s2_id, s3_id = step_ids

    async def fake_repair(failed_step, failure_reason, user_request=""):
        return [{
            "description": "补配置文件", "tool": "file_creator",
            "inputs": {"path": "config/needed.yaml", "content": "key: value"},
            "acceptance": [{"type": "file_exists", "path": "config/needed.yaml", "kind": "file"}],
        }]

    monkeypatch.setattr(server_mod.state, "designer", SimpleNamespace(generate_repair_steps=fake_repair))

    result = await server_mod.replan_plan(plan_id)
    assert result["success"] is True
    new_branch = result["new_branch"]

    assert db.get_plan_status(plan_id) == "Pending"
    plan = db.get_plan(plan_id)
    assert plan.current_branch == new_branch

    rows = db.get_steps(plan_id)
    new_rows = [r for r in rows if int(r.get("branch_version") or 1) == new_branch and not r.get("is_obsolete")]
    repair_rows = [r for r in new_rows if r["tool"] == "file_creator" and "config/needed.yaml" in r["inputs"]]
    assert len(repair_rows) == 1, "修复步骤应写入新分支"
    repair = repair_rows[0]
    # 修复步骤依赖 = 锚点原依赖（s1 已成功）→ 立即可执行
    assert json.loads(repair["depends_on"]) == [s2_id and str(step_ids[0])]
    # F 克隆依赖 = 原依赖 + Rn
    f_clone = next(r for r in new_rows if r.get("parent_step_id") == s2_id)
    f_deps = json.loads(f_clone["depends_on"])
    assert repair["step_id"] in f_deps
    # 后续克隆步骤依赖原链（s3 克隆依赖 s2 原 id → 父映射解析到 F' 状态）
    s3_clone = next(r for r in new_rows if r.get("parent_step_id") == s3_id)
    assert json.loads(s3_clone["depends_on"]) == [s2_id]

    # 依赖感知取步：修复步骤应最先就绪（s1 Success，F' 等 Rn）
    first = db.select_next_batch(plan_id, branch_version=new_branch)
    assert first["steps"], "修复步骤应立即可执行"
    assert first["steps"][0]["step_id"] == repair["step_id"]


@pytest.mark.asyncio
async def test_replan_linearizes_legacy_plan(tmp_path, monkeypatch):
    """旧线性计划（无依赖）replan：克隆链显式化，修复步骤插队后顺序仍正确。"""
    from api import server as server_mod

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    plan_id, step_ids = _save_plan_with_failure(db, tmp_path, linear=True)
    _, s2_id, s3_id = step_ids

    async def fake_repair(failed_step, failure_reason, user_request=""):
        return [{
            "description": "补文件", "tool": "file_creator",
            "inputs": {"path": "fix.txt", "content": "fix"},
            "acceptance": [{"type": "file_exists", "path": "fix.txt", "kind": "file"}],
        }]

    monkeypatch.setattr(server_mod.state, "designer", SimpleNamespace(generate_repair_steps=fake_repair))
    result = await server_mod.replan_plan(plan_id)
    assert result["success"]

    new_branch = result["new_branch"]
    rows = [r for r in db.get_steps(plan_id)
            if int(r.get("branch_version") or 1) == new_branch and not r.get("is_obsolete")]
    rows.sort(key=lambda r: int(r["step_order"]))
    # 全部克隆行都有显式依赖（线性链显式化）
    deps_map = {r["step_id"]: json.loads(r["depends_on"] or "[]") for r in rows}
    assert all(deps_map.values()), "克隆链必须显式化"

    # 串行取步顺序 = R → F' → s3'（依赖驱动）
    order_executed = []
    for _ in range(6):
        sel = db.select_next_batch(plan_id, branch_version=new_branch)
        step = sel["steps"][0] if sel["steps"] else None
        if not step:
            break
        order_executed.append(step["description"])
        db.update_step(step["step_id"], "Success")
    assert order_executed[0] == "补文件"
    assert order_executed.index("失败步骤") < order_executed.index("第三步")


@pytest.mark.asyncio
async def test_replan_rejects_non_failed_plan(tmp_path):
    from api import server as server_mod

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    plan_id, _ = _save_plan_with_failure(db, tmp_path)
    db.update_plan_state_machine(plan_id, "Completed")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await server_mod.replan_plan(plan_id)
    assert exc_info.value.status_code == 409
