"""
plan_update WebSocket 广播链路测试。

覆盖：
- get_plan_status_summary：载荷构成、废弃行过滤、计划缺失返回 None
- broadcast_plan_update：载荷正确、逐客户端容错、摘要读取失败静默
- 端点集成：skip-step 触发含 Skipped 步骤状态的 plan_update 事件
"""

import asyncio
import sys
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from database import Database, DatabaseError
from models import (
    DesignDocument,
    DesignPart1,
    Architecture,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    StepStatus,
    ToolAssessment,
)


def _seed_plan(db: Database, workdir: Path):
    design = DesignDocument(
        design_id=uuid.uuid4(),
        user_request="plan_update 广播测试",
        timestamp=datetime.now(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
        user_approval=True,
    )
    assert db.save_design(design)

    steps = [
        Step(
            step_id=uuid.uuid4(),
            order=i + 1,
            description=f"步骤{i + 1}",
            tool="file_creator",
            inputs={"path": str(workdir / f"f{i}.txt"), "content": str(i)},
            check_criteria="手动检查",
            status=StepStatus.PENDING,
        )
        for i in range(2)
    ]
    plan = ExecutionPlan(
        plan_id=uuid.uuid4(),
        design_id=design.design_id,
        created_at=datetime.now(),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(segment_id=uuid.uuid4(), description="seg", steps=steps)],
        implementation_list=[],
        dynamic_insertions=[],
    )
    assert db.save_plan(plan)
    return plan, steps


class FakeWebSocket:
    """捕获 send_json 的伪 WebSocket 客户端。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("connection gone")
        self.sent.append(payload)


@pytest.fixture()
def broadcast_env(monkeypatch, tmp_path):
    """构造带临时文件 Database 的 server.state 环境 + 伪客户端列表。

    不用 :memory:——每个连接拿到的是独立的内存库，建表会丢失。
    """
    import api.server as server_mod

    db = Database(str(tmp_path / "eas.db"))
    monkeypatch.setattr(server_mod.state, "database", db, raising=False)
    clients = []
    monkeypatch.setattr(server_mod.state, "websocket_clients", clients, raising=False)
    return server_mod, db, clients


def test_get_plan_status_summary_filters_obsolete(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    plan, steps = _seed_plan(db, tmp_path / "w")
    # 模拟回滚残留：同计划插入一条废弃克隆行
    with closing(db._get_connection()) as conn:
        conn.execute(
            "INSERT INTO steps (step_id, plan_id, segment_id, step_order, description,"
            " tool, inputs, check_criteria, outputs, status, branch_version, is_obsolete)"
            " VALUES (?, ?, ?, 9, '废弃克隆', 'file_creator', '{}', '', NULL, 'Pending', 9, 1)",
            (str(uuid.uuid4()), str(plan.plan_id), str(plan.segments[0].segment_id) if not isinstance(plan.segments[0], dict) else plan.segments[0]["segment_id"]),
        )
        conn.commit()

    summary = db.get_plan_status_summary(str(plan.plan_id))
    assert summary is not None
    assert summary["status"] == PlanStatus.PENDING.value
    assert [s["step_id"] for s in summary["steps"]] == [str(s.step_id) for s in steps]
    assert all(s["status"] == "Pending" for s in summary["steps"])


def test_get_plan_status_summary_missing_plan(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    assert db.get_plan_status_summary(str(uuid.uuid4())) is None


def test_broadcast_plan_update_payload(broadcast_env, tmp_path):
    server_mod, db, clients = broadcast_env
    plan, steps = _seed_plan(db, tmp_path / "w")

    good, broken = FakeWebSocket(), FakeWebSocket(fail=True)
    clients.extend([good, broken])

    asyncio.run(server_mod.broadcast_plan_update(str(plan.plan_id)))

    # 坏客户端被摘除，好客户端收到一条 plan_update
    assert broken not in clients
    assert len(good.sent) == 1
    payload = good.sent[0]
    assert payload["type"] == "plan_update"
    assert payload["plan_id"] == str(plan.plan_id)
    assert payload["status"] == PlanStatus.PENDING.value
    assert [s["step_id"] for s in payload["steps"]] == [str(s.step_id) for s in steps]


def test_broadcast_plan_update_silent_on_summary_failure(broadcast_env, tmp_path):
    server_mod, db, clients = broadcast_env

    class ExplodingDb:
        def get_plan_status_summary(self, plan_id):
            raise DatabaseError("查询计划状态摘要失败: boom")

    monkey_target = server_mod
    monkey_target.state.database = ExplodingDb()
    client = FakeWebSocket()
    clients.append(client)

    # 摘要读取失败不得向上抛（广播是观察性副作用）
    asyncio.run(server_mod.broadcast_plan_update("whatever"))
    assert client.sent == []


def test_skip_step_endpoint_broadcasts_plan_update(broadcast_env, tmp_path):
    server_mod, db, clients = broadcast_env
    plan, steps = _seed_plan(db, tmp_path / "w")

    client = FakeWebSocket()
    clients.append(client)

    # 直接调用端点函数：require_token 是 FastAPI 依赖层职责，
    # 单测关注的是端点逻辑与广播副作用，不走 TestClient（其 lifespan
    # 会用真实 data/eas.db 覆盖 monkeypatch 的 state.database）。
    resp = asyncio.run(
        server_mod.skip_step(plan_id=str(plan.plan_id), step_id=str(steps[0].step_id))
    )
    assert resp["success"] is True

    updates = [p for c in clients for p in c.sent if p.get("type") == "plan_update"]
    assert updates, "skip-step 必须触发 plan_update 广播"
    latest = updates[-1]
    assert latest["plan_id"] == str(plan.plan_id)
    skipped = {s["step_id"]: s["status"] for s in latest["steps"]}
    assert skipped[str(steps[0].step_id)] == "Skipped"
    assert skipped[str(steps[1].step_id)] == "Pending"
