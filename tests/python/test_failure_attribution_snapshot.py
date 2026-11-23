import json
import sqlite3
import uuid
from datetime import datetime, timezone

from database import Database
from models import (
    Architecture,
    AttemptStatus,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    RunStatus,
    Step,
    StepStatus,
    ToolAssessment,
)


def _plan(db, *, with_dependency=True):
    design_id = uuid.uuid4()
    db.save_design(DesignDocument(
        design_id=design_id,
        user_request="snapshot test",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[],
        ),
    ))
    plan_id = uuid.uuid4()
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    steps = [
        Step(
            step_id=first_id, order=1, description="first", tool="file_creator",
            inputs={"path": "a.txt"}, check_criteria="exists",
            status=StepStatus.PENDING,
        ),
        Step(
            step_id=second_id, order=2, description="second", tool="test_runner",
            inputs={"path": "tests"}, check_criteria="passed",
            status=StepStatus.PENDING,
            depends_on=[str(first_id)] if with_dependency else [],
        ),
    ]
    db.save_plan(ExecutionPlan(
        plan_id=plan_id, design_id=design_id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(
            segment_id=uuid.uuid4(), description="segment", steps=steps,
        )],
        implementation_list=[], dynamic_insertions=[],
    ))
    return str(plan_id), str(first_id), str(second_id)


def _start(db, plan_id):
    return db.start_execution_run(
        plan_id, (PlanStatus.PENDING.value,), owner_token="snapshot-owner",
    )


def test_run_start_freezes_canonical_active_workgraph(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, second_id = _plan(db)
    run = _start(db, plan_id)

    assert run["graph_snapshot_hash"].startswith("sha256:")
    assert run["graph_snapshot_complete"] is True
    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    assert snapshot["completeness"]["graph_snapshot"] == "valid"
    assert snapshot["completeness"]["analysis_ready"] is False
    assert [item["step_id"] for item in snapshot["steps"]] == [first_id, second_id]
    assert snapshot["steps"][1]["depends_on"] == [first_id]


def test_graph_snapshot_is_immutable_after_live_plan_changes(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, second_id = _plan(db)
    run = _start(db, plan_id)

    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE steps SET depends_on = '[]', tool = 'manual_tool' WHERE step_id = ?",
            (second_id,),
        )

    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    second = next(item for item in snapshot["steps"] if item["step_id"] == second_id)
    assert second["depends_on"] == [first_id]
    assert second["tool"] == "test_runner"


def test_failed_run_snapshot_is_analysis_ready_only_with_terminal_attempts(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, _ = _plan(db)
    run = _start(db, plan_id)
    attempt = db.create_execution_attempt(run["run_id"], plan_id, first_id, 1)
    assert db.finish_execution_attempt(
        attempt["attempt_id"], AttemptStatus.FAILED.value,
        outputs={"success": False, "check_status": "failed"},
        run_id=run["run_id"], owner_token=run["owner_token"],
    )
    assert db.finish_execution_run(
        run["run_id"], run["owner_token"], RunStatus.FAILED.value,
        failure_reason="test failure",
    )

    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    assert snapshot["completeness"]["core_analysis_ready"] is True
    assert snapshot["completeness"]["analysis_ready"] is False
    assert snapshot["completeness"]["attempts_window_complete"] is True
    assert snapshot["completeness"]["attempt_recording_completeness"] == "not_proven_fail_open_writer"
    assert snapshot["completeness"]["lifecycle_consistent"] is True
    assert snapshot["attempts"][0]["attempt_id"] == attempt["attempt_id"]


def test_attempt_limit_uses_limit_plus_one_and_marks_truncation(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, _ = _plan(db)
    run = _start(db, plan_id)
    for attempt_no in range(1, 4):
        db.create_execution_attempt(run["run_id"], plan_id, first_id, attempt_no)

    snapshot = db.get_failure_attribution_snapshot(run["run_id"], attempt_limit=2)
    assert len(snapshot["attempts"]) == 3  # two rows plus one truncation sentinel
    assert snapshot["completeness"]["attempts_truncated"] is True
    assert snapshot["completeness"]["analysis_ready"] is False


def test_unscoped_legacy_events_and_audits_are_counted_but_never_attached(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, _ = _plan(db)
    run = _start(db, plan_id)
    db.add_step_event(first_id, plan_id, "legacy_event", {"secret": "do-not-copy"})
    db.save_audit_log(
        tool_id="command_runner", instruction="run_command",
        inputs={"command": "echo secret"}, plan_id=plan_id,
        step_id=first_id, success=True,
    )

    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    assert snapshot["events"] == []
    assert snapshot["completeness"]["events_run_scoped"] is False
    assert snapshot["completeness"]["audit_logs_run_scoped"] is False
    assert snapshot["auxiliary"]["unscoped_legacy"]["step_events_count_not_used"] == 1
    assert snapshot["auxiliary"]["unscoped_legacy"]["audit_logs_count_not_used"] == 1
    assert "do-not-copy" not in json.dumps(snapshot)
    assert "echo secret" not in json.dumps(snapshot)


def test_legacy_run_without_graph_snapshot_fails_closed(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, _, _ = _plan(db)
    run = _start(db, plan_id)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE execution_runs SET graph_snapshot_json = NULL, graph_snapshot_hash = NULL WHERE run_id = ?",
            (run["run_id"],),
        )

    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    assert snapshot["completeness"]["graph_snapshot"] == "missing_legacy_snapshot"
    assert snapshot["steps"] == []
    assert snapshot["completeness"]["analysis_ready"] is False


def test_snapshot_missing_run_returns_none(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    assert db.get_failure_attribution_snapshot("not-a-run") is None


def test_graph_hash_mismatch_fails_closed(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, _, _ = _plan(db)
    run = _start(db, plan_id)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE execution_runs SET graph_snapshot_json = '{}' WHERE run_id = ?",
            (run["run_id"],),
        )

    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    assert snapshot["completeness"]["graph_snapshot"] == "hash_mismatch"
    assert snapshot["steps"] == []
    assert snapshot["completeness"]["core_analysis_ready"] is False


def test_attempt_outside_run_time_window_blocks_core_readiness(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, _ = _plan(db)
    run = _start(db, plan_id)
    attempt = db.create_execution_attempt(run["run_id"], plan_id, first_id, 1)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE execution_attempts SET started_at = ? WHERE attempt_id = ?",
            ("2000-01-01T00:00:00+00:00", attempt["attempt_id"]),
        )
    assert db.finish_execution_attempt(
        attempt["attempt_id"], AttemptStatus.FAILED.value,
        outputs={"success": False}, run_id=run["run_id"],
        owner_token=run["owner_token"],
    )
    assert db.finish_execution_run(
        run["run_id"], run["owner_token"], RunStatus.FAILED.value,
    )

    snapshot = db.get_failure_attribution_snapshot(run["run_id"])
    assert snapshot["completeness"]["lifecycle_consistent"] is False
    assert any(
        item["code"] == "attempt_start_outside_run"
        for item in snapshot["completeness"]["timestamp_violations"]
    )
    assert snapshot["completeness"]["core_analysis_ready"] is False


def test_snapshot_never_mixes_attempts_from_two_runs_of_same_plan(tmp_path):
    db = Database(str(tmp_path / "snapshot.db"))
    plan_id, first_id, _ = _plan(db)
    run1 = _start(db, plan_id)
    attempt1 = db.create_execution_attempt(run1["run_id"], plan_id, first_id, 1)
    assert db.finish_execution_attempt(
        attempt1["attempt_id"], AttemptStatus.FAILED.value,
        outputs={"success": False}, run_id=run1["run_id"],
        owner_token=run1["owner_token"],
    )
    assert db.finish_execution_run(
        run1["run_id"], run1["owner_token"], RunStatus.FAILED.value,
    )
    assert db.update_plan_state_machine(plan_id, PlanStatus.PENDING.value)

    run2 = _start(db, plan_id)
    attempt2 = db.create_execution_attempt(run2["run_id"], plan_id, first_id, 1)
    snapshot1 = db.get_failure_attribution_snapshot(run1["run_id"])
    snapshot2 = db.get_failure_attribution_snapshot(run2["run_id"])

    assert [row["attempt_id"] for row in snapshot1["attempts"]] == [attempt1["attempt_id"]]
    assert [row["attempt_id"] for row in snapshot2["attempts"]] == [attempt2["attempt_id"]]
