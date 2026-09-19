"""隔离的 checkpoint 恢复验收入口。

该脚本使用临时 cwd/数据库/工作区，不修改仓库运行态；步骤 1 的第一次
失败由真实执行器执行一个受控失败命令构造，恢复时只替换该步骤输入，仍走
真实 executor/tool 状态机。输出 JSON 是脱敏的结构化验收证据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from api import server
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
    ToolRequirement,
)


EXPECTED_ARTIFACT = "checkpoint recovery accepted\n"


def _status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


async def _run(output_path: Path) -> int:
    result: dict[str, Any] = {"schema": "checkpoint-recovery/v2", "checks": [], "failures": []}
    failures: list[str] = result["failures"]
    original_execute = server._execute_step_with_state_machine
    original_cwd = Path.cwd()
    old_workspace = os.environ.get("EAS_WORKSPACE_ROOT")
    # 数据库路径不再跟随 cwd（paths.resolve_db_path 固定到仓库根 data/eas.db），
    # 必须显式指到隔离目录，否则这次验收会把设计书/计划写进真实数据库。
    old_db_path = os.environ.get("EAS_DB_PATH")

    with tempfile.TemporaryDirectory(prefix="harness-checkpoint-", ignore_cleanup_errors=True) as temp_name:
        isolated_root = Path(temp_name)
        artifact = isolated_root / "workspace" / "final-artifact.txt"
        (isolated_root / "workspace").mkdir()
        os.environ["EAS_WORKSPACE_ROOT"] = str(isolated_root / "workspace")
        os.environ["EAS_DB_PATH"] = str(isolated_root / "data" / "eas.db")
        os.chdir(isolated_root)
        try:
            async with server.lifespan(server.app):
                db = server.state.database
                _check(Path(db.db_path).is_relative_to(isolated_root), f"database is not isolated: {db.db_path}", failures)
                design_id = uuid.uuid4()
                design = DesignDocument(
                    design_id=design_id,
                    user_request="隔离 checkpoint 恢复验收",
                    timestamp=datetime.now(),
                    part1_design=DesignPart1(
                        architecture=Architecture(components=["executor"], interactions=["step"]),
                        difficulty=Difficulty(overall="low", breakdown={}),
                    ),
                    part2_tool_assessment=ToolAssessment(
                        required_tools=[ToolRequirement(tool_id="file_creator", availability="available")],
                        overall_tool_completeness=1.0,
                        risks=[],
                    ),
                    user_approval=True,
                )
                if not db.save_design(design):
                    failures.append("save_design returned false")
                    return 1

                sid1, sid2, plan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                steps = [
                    Step(
                        step_id=sid1,
                        order=1,
                        description="controlled command failure then recovery",
                        tool="command_runner",
                        inputs={"command": f'{sys.executable} -c "import sys; sys.exit(7)"', "cwd": str(isolated_root / "workspace")},
                        check_criteria="manual",
                        max_retries=0,
                        status=StepStatus.PENDING,
                    ),
                    Step(
                        step_id=sid2,
                        order=2,
                        description="write final artifact",
                        tool="file_creator",
                        inputs={"path": str(artifact), "content": EXPECTED_ARTIFACT},
                        check_criteria="manual",
                        status=StepStatus.PENDING,
                    ),
                ]
                plan = ExecutionPlan(
                    plan_id=plan_id,
                    design_id=design_id,
                    created_at=datetime.now(),
                    status=PlanStatus.PENDING,
                    segments=[PlanSegment(segment_id=uuid.uuid4(), description="checkpoint", steps=steps)],
                    implementation_list=[],
                    dynamic_insertions=[],
                )
                if not db.save_plan(plan):
                    failures.append("save_plan returned false")
                    return 1

                calls: list[str] = []
                recovery_started = False

                async def controlled_execute(current_plan, step):
                    nonlocal recovery_started
                    step_id = str(step["step_id"])
                    calls.append(step_id)
                    if recovery_started and step_id == str(sid1):
                        recovered = dict(step)
                        recovered["inputs"] = {
                            "command": f'{sys.executable} -c "print(\'checkpoint recovered\')"',
                            "cwd": str(isolated_root / "workspace"),
                        }
                        return await original_execute(current_plan, recovered)
                    result = await original_execute(current_plan, step)
                    if not recovery_started and step_id == str(sid1) and not result.get("success"):
                        # Execute the real command runner, but make this deliberate
                        # first-run failure terminal so the checkpoint is preserved.
                        result["retryable"] = False
                    return result

                server._execute_step_with_state_machine = controlled_execute
                await server.execute_plan_background(db.get_plan(str(plan_id)))
                first_plan = db.get_plan(str(plan_id))
                first_runs = db.list_execution_runs(str(plan_id))
                first_steps = db.get_steps(str(plan_id))
                first_events = db.get_step_events(str(plan_id))

                recovery_started = True
                await server.execute_plan_background(db.get_plan(str(plan_id)))
                final_plan = db.get_plan(str(plan_id))
                all_runs = db.list_execution_runs(str(plan_id))
                final_steps = db.get_steps(str(plan_id))
                all_events = db.get_step_events(str(plan_id))
                attempts = [
                    attempt
                    for run in all_runs
                    for attempt in db.list_execution_attempts(run["run_id"])
                ]

                first_checkpoint = str(first_plan.resume_from_step_id) if first_plan.resume_from_step_id else None
                final_checkpoint = str(final_plan.resume_from_step_id) if final_plan.resume_from_step_id else None
                result.update({
                    "plan_id": str(plan_id),
                    "first": {
                        "plan_status": _status(first_plan.status),
                        "run_statuses": [run["status"] for run in first_runs],
                        "step_statuses": [step["status"] for step in first_steps],
                        "resume_from_step_id": first_checkpoint,
                        "event_types": [event["event_type"] for event in first_events],
                    },
                    "second": {
                        "plan_status": _status(final_plan.status),
                        "run_statuses": [run["status"] for run in all_runs],
                        "step_statuses": [step["status"] for step in final_steps],
                        "resume_from_step_id": final_checkpoint,
                        "event_types": [event["event_type"] for event in all_events],
                        "attempt_statuses": [attempt["status"] for attempt in attempts],
                        "calls": calls,
                        "artifact_exists": artifact.exists(),
                        "artifact_content": artifact.read_text(encoding="utf-8") if artifact.exists() else None,
                    },
                })
                _check(_status(first_plan.status) == "Failed", "first plan is not Failed", failures)
                _check(first_runs and first_runs[0]["status"] == "Failed", "first Run is not Failed", failures)
                _check([step["status"] for step in first_steps] == ["Failed", "Pending"], "first step statuses are not Failed/Pending", failures)
                _check(first_checkpoint == str(sid1), "first checkpoint does not point to step 1", failures)
                _check(_status(final_plan.status) == "Completed", "recovered plan is not Completed", failures)
                _check(all_runs and all_runs[0]["status"] == "Completed", "recovery Run is not Completed", failures)
                _check([step["status"] for step in final_steps] == ["Success", "Success"], "recovered steps are not both Success", failures)
                _check(final_checkpoint is None, "checkpoint was not cleared", failures)
                _check("failed" in [event["event_type"] for event in all_events], "missing failed audit event", failures)
                _check("success" in [event["event_type"] for event in all_events], "missing success audit event", failures)
                _check(any(attempt["status"] == "Succeeded" for attempt in attempts), "missing succeeded attempt", failures)
                _check(calls == [str(sid1), str(sid1), str(sid2)], "recovery did not resume at step 1", failures)
                _check(artifact.exists() and artifact.read_text(encoding="utf-8") == EXPECTED_ARTIFACT, "final artifact mismatch", failures)
        finally:
            server._execute_step_with_state_machine = original_execute
            os.chdir(original_cwd)
            if old_workspace is None:
                os.environ.pop("EAS_WORKSPACE_ROOT", None)
            else:
                os.environ["EAS_WORKSPACE_ROOT"] = old_workspace
            if old_db_path is None:
                os.environ.pop("EAS_DB_PATH", None)
            else:
                os.environ["EAS_DB_PATH"] = old_db_path

    result["checks"] = ["isolated_database", "isolated_workspace", "failed_checkpoint", "same_plan_recovery", "runs", "attempts", "audit_events", "artifact"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated checkpoint recovery acceptance")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "out" / "live-checkpoint-acceptance.json")
    args = parser.parse_args()
    return asyncio.run(_run(args.output.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
