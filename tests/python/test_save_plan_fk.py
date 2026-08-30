# -*- coding: utf-8 -*-
"""回归：失败后重新审批（save_plan 幂等重存）不应撞 step_events 外键（第 2 期观察发现）。"""

import asyncio

import pytest

from database import Database
from designer import DefaultDesigner
from llm_client import DeterministicProvider, LLMClient

from test_full_flow_design import _VALID_PLAN_JSON, _design_document, _make_designer  # noqa: F401


@pytest.mark.asyncio
async def test_save_plan_idempotent_after_step_events(tmp_path):
    """失败后重新审批：计划重存前必须先清 step_events，否则 FK 冲突 500。"""
    db = Database(str(tmp_path / "db.sqlite"))
    designer, _ = _make_designer(_VALID_PLAN_JSON)
    design = _design_document()  # 同一个 design 对象：设计与计划必须引用同一 design_id
    plan = await designer.generate_plan(design)

    db.save_design(design)
    db.save_plan(plan)

    # 模拟一次失败执行：给每个步骤留下事件（step_events 同时 FK 引用 plans 和 steps）
    steps = db.get_steps(str(plan.plan_id))
    assert steps
    for s in steps:
        assert db.add_step_event(s["step_id"], str(plan.plan_id), "failed", {"reason": "x"})

    # 重新保存（失败后重审批的真实路径）→ 修复前会撞 FOREIGN KEY constraint failed
    db.save_plan(plan)  # 不抛异常即为通过

    # 旧事件已清、计划与步骤仍在
    assert db.get_plan_status(str(plan.plan_id)) == "Pending"
    assert len(db.get_steps(str(plan.plan_id))) == len(steps)
