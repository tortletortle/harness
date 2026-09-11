# -*- coding: utf-8 -*-
"""review/diagnosis agent 注册与 Capability 管道、最终交付报告回归。"""
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tools import AgentRuntimeContext, SingleTurnAgentTool
from agent_tools import FakeAgentTool
from authorization import AuthorizationManager
from capabilities import CapabilityRegistry, CapabilityRequest
from database import Database


def _registry_with_agents(tmp_path):
    auth = AuthorizationManager(workspace_root=str(tmp_path))
    llm_client = None
    registry = CapabilityRegistry()
    registry.register(
        SingleTurnAgentTool(
            agent_id="review_agent",
            description="代码审查 Agent（测试）",
            llm_client=llm_client,
            authorization_manager=auth,
            provider_name="fake",
            model="fake-model",
        )
    )
    registry.register(
        SingleTurnAgentTool(
            agent_id="diagnosis_agent",
            description="故障诊断 Agent（测试）",
            llm_client=llm_client,
            authorization_manager=auth,
            provider_name="fake",
            model="fake-model",
        )
    )
    return registry, auth


def test_review_and_diagnosis_agents_registered(tmp_path):
    """两个新 agent 以 agent_tool kind 注册，Designer 工具目录可见。"""
    registry, _ = _registry_with_agents(tmp_path)
    for agent_id in ("review_agent", "diagnosis_agent"):
        capability = registry.get(agent_id)
        assert capability is not None
        spec = capability.spec()
        assert spec.kind == "agent_tool"
        assert spec.can_spawn_agents is False


def test_diagnosis_agent_runs_through_capability_pipeline(tmp_path, monkeypatch):
    """diagnosis_agent 经 Grant 管道执行：fake 模型返回结构化诊断。"""
    registry, auth = _registry_with_agents(tmp_path)

    class _FakeLLM:
        async def chat(self, **kwargs):
            content = json.dumps({
                "status": "completed",
                "output": {"root_cause": "config missing",
                           "recommended_action": "repair"},
                "artifacts": [], "facts": [], "error": None,
            })
            return SimpleNamespace(content=content, usage={})

    monkeypatch.setattr("agent_tools.GrantLLMClient", lambda *a, **k: _FakeLLM())

    grant = auth.issue_for_step(
        plan_id="p1",
        step=SimpleNamespace(step_id="s1", tool="diagnosis_agent", inputs={}),
        workspace_root=str(tmp_path),
        extra_allowed_tools=[],
        limits=None,
    )
    context = AgentRuntimeContext(
        llm_client=None, authorization_manager=auth,
        provider_name="fake", model="fake-model", grant_id=grant.grant_id,
        system_prompt="", plan_id="p1",
    )
    request = CapabilityRequest(
        work_item_id="s1", goal="诊断失败",
        inputs={"step_description": "跑测试", "failure_reason": "exit 1"},
        artifact_refs=[], allowed_tools=[], acceptance=[],
        grant_id=grant.grant_id,
    )
    result = asyncio.run(registry.run(
        "diagnosis_agent", request, context,
    ))
    assert result.status == "completed"
    assert result.output["recommended_action"] == "repair"


def test_final_report_generated_on_completion(tmp_path, monkeypatch):
    """计划 Completed 后调用 _generate_final_report：报告落 artifacts 表（kind=delivery_report）。"""
    from datetime import datetime
    from uuid import uuid4

    from api import server as server_mod
    from models import (
        Architecture, DesignDocument, DesignPart1, Difficulty,
        ExecutionPlan, PlanSegment, PlanStatus, Step, ToolAssessment,
    )

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db

    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="报告", timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    ))
    step = Step(step_id=uuid4(), order=1, description="写文件", tool="file_creator",
                inputs={}, check_criteria="手动检查", status="Success")
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.COMPLETED,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[step])],
        implementation_list=[],
    )
    db.save_plan(plan)

    class _FakeDesigner:
        async def generate_summary(self, plan, results):
            return "# 交付报告\n全部完成。"

    server_mod.state.designer = _FakeDesigner()
    server_mod.state.llm_client = object()  # 非 None 即可
    asyncio.run(server_mod._generate_final_report(plan))

    artifacts = db.list_artifacts(plan_id=str(plan.plan_id))
    reports = [a for a in artifacts if a["kind"] == "delivery_report"]
    assert len(reports) == 1
    content = db.read_artifact_content(reports[0]["artifact_id"])
    assert "交付报告".encode("utf-8") in content


def test_final_report_fail_open_without_designer(tmp_path):
    """Designer 缺失时报告生成静默跳过（fail-open）。"""
    from datetime import datetime
    from uuid import uuid4

    from api import server as server_mod
    from models import (
        Architecture, DesignDocument, DesignPart1, Difficulty,
        ExecutionPlan, PlanSegment, PlanStatus, Step, ToolAssessment,
    )

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    server_mod.state.designer = None

    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="x", timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    ))
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.COMPLETED,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[])],
        implementation_list=[],
    )
    db.save_plan(plan)
    asyncio.run(server_mod._generate_final_report(plan))  # 不抛异常即通过
