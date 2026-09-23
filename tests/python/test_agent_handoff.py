# -*- coding: utf-8 -*-
"""智能体任务交接（handoff）契约回归。

覆盖四条交接链路契约，都是「步骤与步骤之间」的行为，不依赖真实模型：
1. 下游 agent 自动收到上游步骤的实际产出（summary / facts / 产物引用），
   且 CapabilityRequest.artifact_refs 不再恒为空列表；
2. agent 结果被 executor 包了一层之后，产物登记仍能拿到 file:// 引用；
3. needs_input 写回 Pending + Paused + 恢复断点，而不是被误判 Failed；
4. 最终交付报告读的是数据库权威结果，不是认领执行时的计划快照。
"""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_tools import BaseAgentTool, FakeAgentTool
from api.shared import state
from authorization import AuthorizationManager
from capabilities import CapabilityRegistry, CapabilityResult
from database import Database
from executor import DefaultExecutor
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

TOKEN = "handoff-token-4413"


def _seed_plan(db, specs):
    """specs: [(description, [依赖名], tool)]；返回 (plan, steps, 名字→step_id)。"""
    design_id = uuid4()
    assert db.save_design(DesignDocument(
        design_id=design_id,
        user_request="交接测试",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1, risks=[],
        ),
        user_approval=True,
    ))
    by_name: dict[str, str] = {}
    steps = []
    for order, (name, deps, tool) in enumerate(specs, 1):
        step = Step(
            step_id=uuid4(), order=order, description=name, tool=tool,
            inputs={}, check_criteria="手动检查",
            depends_on=[by_name[dep] for dep in deps],
        )
        by_name[name] = str(step.step_id)
        steps.append(step)
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.PENDING,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=steps)],
        implementation_list=[],
    )
    db.save_plan(plan)
    return plan, steps, by_name


def _registry_with_agent():
    registry = CapabilityRegistry()
    registry.register(FakeAgentTool(
        agent_id="review_agent",
        result=CapabilityResult(status="completed", output={"ok": True}),
    ))
    return registry


def test_handoff_context_carries_upstream_actual_results(tmp_path):
    """交接内容来自上游落库结果，且严格限定在本步骤的直接依赖上。"""
    from api.runtime.step_execution import build_handoff_context

    db = Database(str(tmp_path / "handoff.db"))
    plan, _, ids = _seed_plan(db, [
        ("上游调查", [], "review_agent"),
        ("无关步骤", [], "review_agent"),
        ("下游修复", ["上游调查"], "review_agent"),
    ])
    report = tmp_path / "report.md"
    report.write_text("# 审查结论", encoding="utf-8")
    artifact = f"file://{report.as_posix()}"
    db.update_step_state_machine(
        step_id=ids["上游调查"], status="Success",
        outputs={"agent_status": "completed", "root_cause": "配置缺失"},
        state_snapshot={
            "success": True,
            "output": {"agent_status": "completed", "output": {"detail": "缺 API_KEY"}},
            "agent_facts": [{"fact": "配置缺 API_KEY"}],
            "agent_artifacts": [artifact],
        },
    )
    db.update_step_state_machine(step_id=ids["无关步骤"], status="Success", outputs={"note": "不应交接"})
    db.update_step_state_machine(step_id=ids["下游修复"], status="Running")

    rows = db.get_active_steps_snapshot(str(plan.plan_id), 1)
    downstream = next(r for r in rows if str(r["step_id"]) == ids["下游修复"])
    context = build_handoff_context(downstream, rows, 1)

    assert [entry["step_id"] for entry in context] == [ids["上游调查"]]
    entry = context[0]
    assert "配置缺失" in entry["summary"]
    assert entry["facts"] == [{"fact": "配置缺 API_KEY"}]
    assert entry["artifacts"] == [artifact]
    assert "不应交接" not in json.dumps(context, ensure_ascii=False)


def test_unsuccessful_upstream_is_not_handed_off(tmp_path):
    """上游还在跑/已失败时不交接：依赖是否可执行由调度器判定，不由交接层猜测。"""
    from api.runtime.step_execution import build_handoff_context

    db = Database(str(tmp_path / "handoff_pending.db"))
    plan, _, ids = _seed_plan(db, [("上游", [], "review_agent"), ("下游", ["上游"], "review_agent")])
    rows = db.get_active_steps_snapshot(str(plan.plan_id), 1)
    downstream = next(r for r in rows if str(r["step_id"]) == ids["下游"])
    assert build_handoff_context(downstream, rows, 1) == []


class _RecordingAgent(FakeAgentTool):
    """记录收到的 CapabilityRequest，用于断言交接内容真的到了 agent。"""

    def __init__(self, agent_id, result):
        super().__init__(agent_id=agent_id, result=result)
        self.seen = []

    async def run(self, request, runtime_context):
        self.seen.append(request)
        return await super().run(request, runtime_context)


def test_agent_request_receives_handoff_context_and_artifact_refs(tmp_path):
    """CapabilityRequest 必须带上下游交接内容与产物引用（此前恒为 artifact_refs=[]）。"""
    auth = AuthorizationManager(workspace_root=str(tmp_path))
    registry = CapabilityRegistry()
    agent = _RecordingAgent(
        "review_agent",
        CapabilityResult(status="completed", output={"review": "ok"}),
    )
    registry.register(agent)
    executor = DefaultExecutor(
        llm_client=None,
        model_config={"primary": {"provider": "fake", "model": "fake"}},
        authorization_manager=auth,
        capability_registry=registry,
    )
    notes = tmp_path / "notes.md"
    notes.write_text("上游结论", encoding="utf-8")
    upstream_ref = f"file://{notes.as_posix()}"
    step = Step(
        step_id=uuid4(), order=2, description="复核上游结论", tool="review_agent",
        inputs={
            "upstream_context": [
                {"step_id": "s1", "summary": "上游发现 X", "artifacts": [upstream_ref]},
            ],
        },
        check_criteria="手动检查",
    )
    grant = auth.issue_for_step(
        plan_id="p1", step=step, workspace_root=str(tmp_path),
        extra_allowed_tools=[], limits=None,
    )
    result = asyncio.run(executor._run_agent_capability(
        registry.get("review_agent"), step, "review_agent",
        dict(step.inputs), grant.grant_id, "p1",
    ))

    assert result["success"] is True
    request = agent.seen[0]
    assert request.inputs["upstream_context"][0]["summary"] == "上游发现 X"
    assert request.artifact_refs == [upstream_ref]
    # 交接只传引用：不因为上游提到某个文件就替 agent 扩权。
    assert request.allowed_tools == []
    assert upstream_ref in BaseAgentTool._base_prompt(request)


def test_agent_step_receives_upstream_context_from_runtime(tmp_path, monkeypatch):
    """端到端一步：运行时装步骤行时，把上游落库结果注入 agent 步骤的 inputs。"""
    from api.runtime import step_execution

    db = Database(str(tmp_path / "wire.db"))
    plan, _, ids = _seed_plan(db, [("上游调查", [], "review_agent"), ("下游审查", ["上游调查"], "review_agent")])
    db.update_step_state_machine(
        step_id=ids["上游调查"], status="Success",
        outputs={"summary": "上游结论：配置缺失"},
    )
    captured = {}

    class _StubExecutor:
        async def execute_step(self, step, grant_id=None, plan_id=None):
            captured["step"] = step
            captured["grant_id"] = grant_id
            return {"success": True, "output": {"ok": True}, "check_status": "passed",
                    "error": None, "blocked": False}

    monkeypatch.setattr(step_execution, "state", SimpleNamespace(
        database=db,
        capability_registry=_registry_with_agent(),
        tool_registry=None,
        executor=_StubExecutor(),
        authorization_manager=AuthorizationManager(workspace_root=str(tmp_path)),
        approval_manager=SimpleNamespace(is_required_for_step=lambda tool_id, kind: False),
        notifier=None,
        context_compression={},
        retry_max_attempts=3,
        retry_backoff_factor=2,
    ))
    downstream_row = next(r for r in db.get_steps(str(plan.plan_id))
                          if str(r["step_id"]) == ids["下游审查"])
    result = asyncio.run(step_execution._execute_step_with_state_machine(plan, downstream_row))

    assert result["success"] is True
    context = captured["step"].inputs["upstream_context"]
    assert context[0]["step_id"] == ids["上游调查"]
    assert "配置缺失" in context[0]["summary"]
    events = db.get_step_events(str(plan.plan_id), ids["下游审查"])
    handoff_events = [e for e in events if e["event_type"] == "handoff_context"]
    assert handoff_events, "交接过程必须可审计（step_event handoff_context）"
    assert json.loads(handoff_events[0]["event_data"])["from_steps"] == [ids["上游调查"]]
    # 注入只发生在执行期：计划期 steps.inputs 快照不被改写
    assert "upstream_context" not in json.loads(db.get_steps(str(plan.plan_id))[1]["inputs"])


def test_plain_tool_step_is_not_injected(tmp_path, monkeypatch):
    """普通工具的 inputs 有各自契约，不顺手塞键。"""
    from api.runtime import step_execution

    db = Database(str(tmp_path / "plain.db"))
    plan, _, ids = _seed_plan(db, [("上游调查", [], "review_agent"), ("下游写文件", ["上游调查"], "file_creator")])
    db.update_step_state_machine(step_id=ids["上游调查"], status="Success", outputs={"summary": "上游结论"})
    captured = {}

    class _StubExecutor:
        async def execute_step(self, step, grant_id=None, plan_id=None):
            captured["step"] = step
            return {"success": True, "output": {"path": "a.txt"}, "check_status": "unverified",
                    "error": None, "blocked": False}

    monkeypatch.setattr(step_execution, "state", SimpleNamespace(
        database=db,
        capability_registry=_registry_with_agent(),
        tool_registry=None,
        executor=_StubExecutor(),
        authorization_manager=AuthorizationManager(workspace_root=str(tmp_path)),
        approval_manager=SimpleNamespace(is_required_for_step=lambda tool_id, kind: False),
        notifier=None,
        context_compression={},
        retry_max_attempts=3,
        retry_backoff_factor=2,
    ))
    row = next(r for r in db.get_steps(str(plan.plan_id)) if str(r["step_id"]) == ids["下游写文件"])
    asyncio.run(step_execution._execute_step_with_state_machine(plan, row))
    assert "upstream_context" not in captured["step"].inputs


def test_agent_artifact_registration_unwraps_nested_result(tmp_path, monkeypatch):
    """agent 产物引用在外层 output 的下一层，登记必须拿到它（且只登记一次）。"""
    from api.runtime.artifacts import _register_step_artifacts

    db = Database(str(tmp_path / "artifacts.db"))
    monkeypatch.setattr(state, "database", db)
    plan, _, ids = _seed_plan(db, [("写报告", [], "review_agent")])
    target = tmp_path / "review.md"
    target.write_text("# 审查结论", encoding="utf-8")
    uri = f"file://{target.as_posix()}"
    wrapped = {
        "success": True,
        "check_status": "unverified",
        "check_passed": False,
        "output": {
            "success": True,
            "message": "agent review_agent -> completed",
            "output": {"agent_status": "completed", "path": str(target)},
            "agent_artifacts": [uri],
            "agent_facts": [],
        },
    }
    _register_step_artifacts(str(plan.plan_id), ids["写报告"], wrapped)
    rows = db.list_artifacts(plan_id=str(plan.plan_id))
    assert [r["uri"] for r in rows] == [uri]
    assert rows[0]["step_id"] == ids["写报告"]
    assert rows[0]["size_bytes"] == target.stat().st_size


def test_plain_tool_artifact_registration_still_works(tmp_path, monkeypatch):
    """普通工具（result['output']['path']）的登记路径保持不变。"""
    from api.runtime.artifacts import _register_step_artifacts

    db = Database(str(tmp_path / "artifacts_tool.db"))
    monkeypatch.setattr(state, "database", db)
    plan, _, ids = _seed_plan(db, [("写文件", [], "file_creator")])
    target = tmp_path / "written.txt"
    target.write_text("hello", encoding="utf-8")
    _register_step_artifacts(str(plan.plan_id), ids["写文件"], {
        "success": True, "output": {"success": True, "path": str(target)},
    })
    rows = db.list_artifacts(plan_id=str(plan.plan_id))
    assert [r["uri"] for r in rows] == [f"file://{target.as_posix()}"]


@pytest.mark.parametrize("tool", ["review_agent", "coding_agent"])
def test_agent_needs_input_is_flagged_not_failed(tmp_path, tool):
    """needs_input 必须带上明确标记与「不可自动重试」，否则上层只能当成失败。"""
    registry = CapabilityRegistry()
    registry.register(_RecordingAgent(
        tool, CapabilityResult(status="needs_input", output={"questions": ["目标仓库地址？"]}),
    ))
    auth = AuthorizationManager(workspace_root=str(tmp_path))
    executor = DefaultExecutor(
        llm_client=None,
        model_config={"primary": {"provider": "fake", "model": "fake"}},
        authorization_manager=auth,
        capability_registry=registry,
    )
    step = Step(step_id=uuid4(), order=1, description="补齐信息", tool=tool,
                inputs={}, check_criteria="手动检查")
    grant = auth.issue_for_step(plan_id="p1", step=step, workspace_root=str(tmp_path),
                                 extra_allowed_tools=[], limits=None)
    result = asyncio.run(executor._run_agent_capability(
        registry.get(tool), step, tool, dict(step.inputs), grant.grant_id, "p1",
    ))
    assert result["needs_input"] is True
    assert result["success"] is False
    assert result["retryable"] is False
    assert result["output"]["agent_status"] == "needs_input"


def test_needs_input_pauses_plan_and_keeps_resume_target(tmp_path, monkeypatch):
    """缺信息 → 步骤 Pending + 计划 Paused + 恢复断点，绝不写 Failed。"""
    from api import server as server_mod
    from api.runtime import step_execution

    db = Database(str(tmp_path / "needs.db"))
    plan, _, ids = _seed_plan(db, [("需要补充输入", [], "review_agent")])
    step_id = ids["需要补充输入"]
    monkeypatch.setattr(state, "database", db)
    monkeypatch.setattr(state, "capability_registry", None)
    monkeypatch.setattr(state, "tool_registry", None)

    async def _needs_input_step(plan_arg, step_arg):
        return {
            "success": False, "needs_input": True, "blocked": False,
            "check_status": "unverified", "check_passed": False, "error": None,
            "message": "agent review_agent -> needs_input",
            "output": {
                "success": False, "agent_status": "needs_input",
                "output": {"questions": ["目标仓库地址是什么？"]}, "error": None,
            },
            "duration_ms": 7,
        }

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", _needs_input_step)
    run = db.start_execution_run(str(plan.plan_id), from_statuses=(PlanStatus.PENDING.value,), trigger="execute")
    assert run is not None
    step_row = db.get_steps(str(plan.plan_id))[0]

    outcome, stopped_step = asyncio.run(step_execution._execute_step_to_completion(
        plan, step_row, run["run_id"], run["owner_token"], 1, False, set(), lambda msg: None,
    ))

    assert (outcome, stopped_step) == ("stop", step_id)
    row = db.get_steps(str(plan.plan_id))[0]
    assert row["status"] == StepStatus.PENDING.value
    outputs = json.loads(row["outputs"])
    assert outputs["needs_input"] is True
    assert "目标仓库地址" in outputs["question"]
    assert db.get_plan_status(str(plan.plan_id)) == PlanStatus.PAUSED.value
    assert str(db.get_plan(str(plan.plan_id)).resume_from_step_id) == step_id
    event_types = [e["event_type"] for e in db.get_step_events(str(plan.plan_id), step_id)]
    assert "needs_input" in event_types
    assert "failed" not in event_types
    assert db.get_steps(str(plan.plan_id))[0]["failure_reason"] in (None, "")
    # 补输入前不该被当成失败步骤：失败步骤查询必须为空（否则收尾会误判 Failed）
    assert db.get_active_failed_steps(str(plan.plan_id), 1) == []


def test_supplied_input_is_persisted_and_step_stays_resumable(tmp_path, monkeypatch):
    """补充输入端点写回 steps.inputs，并保留恢复目标；提交输入不等于授权执行。"""
    from fastapi.testclient import TestClient

    from api.auth import reset_token_cache
    from api.server import app

    monkeypatch.setenv("EAS_API_TOKEN", TOKEN)
    reset_token_cache()
    db = Database(str(tmp_path / "supply.db"))
    monkeypatch.setattr(state, "database", db)
    plan, _, ids = _seed_plan(db, [("需要补充输入", [], "review_agent")])
    step_id = ids["需要补充输入"]
    db.update_step_state_machine(step_id=step_id, status="Pending",
                                 outputs={"needs_input": True, "question": "要哪个仓库？"})
    db.update_plan_state_machine(str(plan.plan_id), PlanStatus.PAUSED.value, resume_from_step_id=step_id)
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        detail = client.get(f"/api/plan/{plan.plan_id}", headers=headers)
        assert detail.status_code == 200
        visible = detail.json()["plan"]
        assert visible["status"] == PlanStatus.PAUSED.value
        assert visible["resume_from_step_id"] == step_id
        assert visible["segments"][0]["steps"][0]["outputs"] == {
            "needs_input": True, "question": "要哪个仓库？"
        }
        resp = client.post(
            f"/api/plan/{plan.plan_id}/steps/{step_id}/input",
            headers=headers,
            json={"input": {"repo_url": "https://example.invalid/repo"},
                  "artifact_refs": ["file://notes/repo.md"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True

        refreshed = client.get(f"/api/plan/{plan.plan_id}", headers=headers)
        assert refreshed.status_code == 200
        assert refreshed.json()["plan"]["segments"][0]["steps"][0]["inputs"]["user_input"] == {
            "repo_url": "https://example.invalid/repo"
        }

        row = db.get_steps(str(plan.plan_id))[0]
        inputs = json.loads(row["inputs"])
        assert inputs["user_input"] == {"repo_url": "https://example.invalid/repo"}
        assert inputs["artifact_refs"] == ["file://notes/repo.md"]
        assert db.get_plan_status(str(plan.plan_id)) == PlanStatus.PAUSED.value
        selection = db.select_next_batch(str(plan.plan_id), branch_version=1,
                                         resume_from_step_id=step_id, max_concurrency=1)
        assert [s["step_id"] for s in selection["steps"]] == [step_id]
        events = [e["event_type"] for e in db.get_step_events(str(plan.plan_id), step_id)]
        assert "input_supplied" in events

        # 计划不在 Paused（已恢复执行）时必须拒绝，避免改写正在跑的步骤输入
        db.update_plan_state_machine(str(plan.plan_id), PlanStatus.RUNNING.value)
        denied = client.post(
            f"/api/plan/{plan.plan_id}/steps/{step_id}/input",
            headers=headers, json={"input": {"x": 1}},
        )
        assert denied.status_code == 409
        # 步骤已完成时同样拒绝
        db.update_plan_state_machine(str(plan.plan_id), PlanStatus.PAUSED.value)
        db.update_step_state_machine(step_id=step_id, status=StepStatus.SUCCESS.value)
        done = client.post(
            f"/api/plan/{plan.plan_id}/steps/{step_id}/input",
            headers=headers, json={"input": {"x": 1}},
        )
        assert done.status_code == 409
    finally:
        monkeypatch.delenv("EAS_API_TOKEN", raising=False)
        reset_token_cache()


def test_supply_input_rejects_unrelated_or_non_waiting_steps(tmp_path, monkeypatch):
    """普通人工暂停不能改写步骤；等待输入时只允许改写恢复目标。"""
    from fastapi.testclient import TestClient

    from api.auth import reset_token_cache
    from api.server import app

    monkeypatch.setenv("EAS_API_TOKEN", TOKEN)
    reset_token_cache()
    db = Database(str(tmp_path / "supply-guard.db"))
    monkeypatch.setattr(state, "database", db)
    plan, _, ids = _seed_plan(db, [
        ("需要输入", [], "review_agent"),
        ("其他步骤", [], "review_agent"),
    ])
    waiting_id, other_id = ids["需要输入"], ids["其他步骤"]
    db.update_plan_state_machine(str(plan.plan_id), PlanStatus.PAUSED.value,
                                 resume_from_step_id=waiting_id)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    try:
        url = f"/api/plan/{plan.plan_id}/steps"
        no_request = client.post(f"{url}/{waiting_id}/input", headers=headers,
                                 json={"input": "没有请求过"})
        assert no_request.status_code == 409

        db.update_step_state_machine(step_id=waiting_id, status="Pending",
                                     outputs={"needs_input": True, "question": "请补充"})
        unrelated = client.post(f"{url}/{other_id}/input", headers=headers,
                                json={"input": "不属于此步骤"})
        assert unrelated.status_code == 409
        other_row = next(r for r in db.get_steps(str(plan.plan_id)) if r["step_id"] == other_id)
        assert json.loads(other_row["inputs"]) == {}

        allowed = client.post(f"{url}/{waiting_id}/input", headers=headers,
                              json={"input": "这里是补充信息"})
        assert allowed.status_code == 200
    finally:
        monkeypatch.delenv("EAS_API_TOKEN", raising=False)
        reset_token_cache()


def test_coding_agent_needs_input_is_not_a_failure(tmp_path, monkeypatch):
    """多轮 coding_agent 同样要保留 needs_input，不能退化成「输出缺少 content」失败。"""
    from authorization import GrantLimits
    from capabilities import CapabilityRegistry
    from coding_agent import CodingAgentTool
    from llm_client import DeterministicProvider, LLMClient
    from tools import get_registry

    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    original = "def greet():\n    return 1\n"
    (tmp_path / "src" / "app.py").write_text(original, encoding="utf-8")
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))

    payload = json.dumps(
        {"status": "needs_input", "output": {"question": "要改哪个函数？"},
         "artifacts": [], "facts": [], "error": None},
        ensure_ascii=False,
    )
    llm = LLMClient()
    llm.register_provider(DeterministicProvider(payload))
    manager = AuthorizationManager(workspace_root=tmp_path)
    registry = CapabilityRegistry()
    registry.register(CodingAgentTool(
        llm_client=llm, authorization_manager=manager, tool_registry=get_registry(),
        provider_name="deterministic", model="test-model", database=None,
    ))
    executor = DefaultExecutor(
        llm_client=llm,
        model_config={"primary": {"provider": "deterministic", "model": "test-model"}},
        tool_registry=get_registry(), authorization_manager=manager, capability_registry=registry,
    )
    step = Step(
        step_id=uuid4(), order=1, description="修改 greet 返回值", tool="coding_agent",
        inputs={"path": "src/app.py", "instruction": "改返回值", "allowed_tools": ["text_editor"]},
        check_criteria="", acceptance=[],
    )
    grant = manager.issue_for_step(
        "plan-needs", step, extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )
    result = asyncio.run(executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-needs"))

    assert result.get("needs_input") is True, result
    assert result["success"] is False
    assert result["retryable"] is False
    assert result["output"]["output"]["agent_status"] == "needs_input"
    assert "要改哪个函数" in json.dumps(result["output"]["output"], ensure_ascii=False)
    # 缺信息阶段绝不写文件
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == original


def test_final_report_reads_authoritative_database_results(tmp_path, monkeypatch):
    """报告必须用数据库最新结果，而不是认领执行时的计划快照。"""
    from api import server as server_mod
    from api.runtime.artifacts import _generate_final_report

    db = Database(str(tmp_path / "report.db"))
    monkeypatch.setattr(server_mod.state, "database", db)
    plan, _, ids = _seed_plan(db, [("写文件", [], "file_creator")])
    stale = db.get_plan(str(plan.plan_id))
    assert stale.segments[0].steps[0].status == StepStatus.PENDING

    db.update_step_state_machine(
        step_id=ids["写文件"], status=StepStatus.SUCCESS.value,
        outputs={"path": "out/a.txt"}, compressed_context="已创建 out/a.txt",
    )
    db.update_plan_state_machine(str(plan.plan_id), PlanStatus.COMPLETED.value)

    captured = {}

    class _StubDesigner:
        async def generate_summary(self, plan_arg, results):
            captured["plan_status"] = plan_arg.status.value
            captured["steps"] = [
                {"status": step.status.value, "outputs": step.outputs, "ctx": step.compressed_context}
                for step in results
            ]
            return "# 交付报告\n全部完成。"

    monkeypatch.setattr(server_mod.state, "designer", _StubDesigner())
    monkeypatch.setattr(server_mod.state, "llm_client", object())
    asyncio.run(_generate_final_report(stale, None))

    assert captured["plan_status"] == PlanStatus.COMPLETED.value
    entry = captured["steps"][0]
    assert entry["status"] == StepStatus.SUCCESS.value
    assert entry["outputs"] == {"path": "out/a.txt"}
    assert entry["ctx"] == "已创建 out/a.txt"
    reports = [a for a in db.list_artifacts(plan_id=str(plan.plan_id)) if a["kind"] == "delivery_report"]
    assert len(reports) == 1


def test_missing_plan_does_not_break_report(tmp_path, monkeypatch):
    """数据库里查不到计划时回退到入参快照（fail-open，不抛异常）。"""
    from api import server as server_mod
    from api.runtime.artifacts import _generate_final_report

    db = Database(str(tmp_path / "report_missing.db"))
    monkeypatch.setattr(server_mod.state, "database", db)
    plan, _, _ = _seed_plan(db, [("写文件", [], "file_creator")])
    seen = {}

    class _StubDesigner:
        async def generate_summary(self, plan_arg, results):
            seen["plan_id"] = str(plan_arg.plan_id)
            return "报告"

    monkeypatch.setattr(server_mod.state, "designer", _StubDesigner())
    monkeypatch.setattr(server_mod.state, "llm_client", object())
    asyncio.run(_generate_final_report(plan, None))
    assert seen["plan_id"] == str(plan.plan_id)


def test_executor_wrapped_snapshot_keeps_facts_and_artifacts(tmp_path):
    """真实链路：state_snapshot = execute_step 返回（output 包裹 capability 结果），
    facts/artifacts 落在 wrapper 层而非顶层——交接不得因此丢失。"""
    from api.runtime.step_execution import build_handoff_context

    db = Database(str(tmp_path / "wrapped.db"))
    plan, _, ids = _seed_plan(db, [
        ("上游编码", [], "coding_agent"),
        ("下游审查", ["上游编码"], "review_agent"),
    ])
    report = tmp_path / "out.py"
    report.write_text("def f(): return 1\n", encoding="utf-8")
    artifact = f"file://{report.as_posix()}"
    model_output = {"agent_status": "completed", "path": str(report), "turns": 2}
    wrapper = {
        "success": True,
        "output": model_output,
        "message": "agent coding_agent -> completed",
        "error": None,
        "blocked": False,
        "agent_facts": [{"key": "coding_agent_turns_used", "value": 2}],
        "agent_artifacts": [artifact],
    }
    db.update_step_state_machine(
        step_id=ids["上游编码"], status="Success",
        outputs={**wrapper, "check_status": "unverified"},
        state_snapshot={"success": True, "output": wrapper, "check_status": "unverified",
                        "error": None, "blocked": False},
    )
    db.update_step_state_machine(step_id=ids["下游审查"], status="Running")

    rows = db.get_active_steps_snapshot(str(plan.plan_id), 1)
    downstream = next(r for r in rows if str(r["step_id"]) == ids["下游审查"])
    context = build_handoff_context(downstream, rows, 1)

    assert [e["step_id"] for e in context] == [ids["上游编码"]]
    entry = context[0]
    assert entry["facts"] == [{"key": "coding_agent_turns_used", "value": 2}]
    assert entry["artifacts"] == [artifact]
    assert "agent_facts" not in entry.get("summary", "")
    assert "agent_artifacts" not in entry.get("summary", "")


def test_handoff_summary_prefers_model_output_over_wrapper(tmp_path):
    """agent 结果被包两层 output 时，摘要取内层模型输出，而非整包 JSON。"""
    from api.runtime.step_execution import build_handoff_context

    db = Database(str(tmp_path / "summary.db"))
    plan, _, ids = _seed_plan(db, [
        ("上游审查", [], "review_agent"),
        ("下游", ["上游审查"], "review_agent"),
    ])
    db.update_step_state_machine(
        step_id=ids["上游审查"], status="Success",
        outputs={
            "success": True,
            "output": {"agent_status": "completed", "结论": "改用参数化查询"},
            "agent_facts": [],
            "agent_artifacts": [],
            "error": None,
            "check_status": "unverified",
        },
        state_snapshot={
            "success": True,
            "output": {
                "success": True,
                "output": {"agent_status": "completed", "结论": "改用参数化查询"},
                "agent_facts": [],
                "agent_artifacts": [],
            },
            "check_status": "unverified",
        },
    )
    db.update_step_state_machine(step_id=ids["下游"], status="Running")
    rows = db.get_active_steps_snapshot(str(plan.plan_id), 1)
    downstream = next(r for r in rows if str(r["step_id"]) == ids["下游"])
    entry = build_handoff_context(downstream, rows, 1)[0]
    assert "改用参数化查询" in entry["summary"]
    assert "agent_facts" not in entry["summary"]

