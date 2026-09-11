# -*- coding: utf-8 -*-
"""Artifact 内容层回归：内容落盘 + SHA-256 校验、API 端点、步骤产物自动登记。"""
from datetime import datetime
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
    ToolAssessment,
)


def _save_plan(db: Database) -> str:
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="art", timestamp=datetime.utcnow(),
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


def test_store_and_read_artifact_content_roundtrip(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    plan_id = _save_plan(db)
    record = db.store_artifact_content(
        "art-1", "你好 artifact".encode("utf-8"),
        plan_id=plan_id, step_id="s1", kind="file", metadata={"note": "x"},
    )
    assert record["content_hash"].startswith("sha256:")
    assert record["size_bytes"] == len("你好 artifact".encode("utf-8"))
    assert record["uri"].startswith("artifact://")

    content = db.read_artifact_content("art-1")
    assert content.decode("utf-8") == "你好 artifact"
    assert db.verify_artifact_hash("art-1") is True
    # 篡改内容 → 哈希不一致
    (tmp_path / "artifacts" / "art-1").write_bytes(b"tampered")
    assert db.verify_artifact_hash("art-1") is False


def test_read_artifact_content_missing_cases(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    assert db.read_artifact_content("nope") is None
    # 仅登记外部引用（无 plan 关联），未入库内容 → None
    db.create_artifact(artifact_id="ext", kind="file", uri="file://somewhere")
    assert db.read_artifact_content("ext") is None
    assert db.verify_artifact_hash("ext") is None


def test_step_output_auto_registration(tmp_path):
    """步骤成功后 output.path / agent file:// 引用自动登记（仅元数据）。"""
    from api import server as server_mod

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    plan_id = _save_plan(db)
    step_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = {
        "success": True,
        "output": {"path": str(tmp_path / "out.txt"), "check_status": "unverified"},
        "agent_artifacts": ["file://src/generated.py"],
    }
    server_mod._register_step_artifacts(plan_id, step_id, result)

    artifacts = db.list_artifacts(plan_id=plan_id)
    uris = {a["uri"] for a in artifacts}
    assert f"file://{str(tmp_path / 'out.txt').replace(chr(92), '/')}" in uris
    assert "file://src/generated.py" in uris
    assert all(a["metadata"].get("source") == "step_execution" for a in artifacts)
