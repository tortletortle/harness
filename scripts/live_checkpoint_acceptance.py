import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "python")

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


async def main():
    async with server.lifespan(server.app):
        await run_acceptance()


async def run_acceptance():
    db = server.state.database
    work = Path("out/live-checkpoint-acceptance")
    work.mkdir(parents=True, exist_ok=True)
    artifact = work / "final-artifact.txt"
    if artifact.exists():
        artifact.unlink()

    design_id = uuid.uuid4()
    design = DesignDocument(
        design_id=design_id,
        user_request="真实 checkpoint 恢复验收",
        timestamp=datetime.now(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["executor"], interactions=["step"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[ToolRequirement(tool_id="file_creator", availability="available")], overall_tool_completeness=1.0, risks=[]
        ),
        user_approval=True,
    )
    assert db.save_design(design)

    sid1, sid2, pid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    steps = [
        Step(
            step_id=sid1,
            order=1,
            description="first checkpoint step",
            tool="manual_tool",
            inputs={"instructions": "intentional failure"},
            check_criteria="manual",
            status=StepStatus.PENDING,
        ),
        Step(
            step_id=sid2,
            order=2,
            description="write final artifact",
            tool="file_creator",
            inputs={"path": str(artifact.resolve()), "content": "checkpoint recovery accepted\n"},
            check_criteria="manual",
            status=StepStatus.PENDING,
        ),
    ]
    plan = ExecutionPlan(
        plan_id=pid,
        design_id=design_id,
        created_at=datetime.now(),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(segment_id=uuid.uuid4(), description="checkpoint", steps=steps)],
        implementation_list=[],
        dynamic_insertions=[],
    )
    assert db.save_plan(plan)

    original = server._execute_step_with_state_machine
    calls = []

    async def first_fail(current_plan, step):
        calls.append(str(step["step_id"]))
        return {
            "success": False,
            "retryable": False,
            "blocked": False,
            "check_status": "unverified",
            "error": "intentional first-run failure",
        }

    server._execute_step_with_state_machine = first_fail
    await server.execute_plan_background(db.get_plan(str(pid)))
    first_plan = db.get_plan(str(pid))
    first_runs = db.list_execution_runs(str(pid))
    first_steps = db.get_steps(str(pid))
    first_events = db.get_step_events(str(pid))

    async def recovery(current_plan, step):
        calls.append(str(step["step_id"]))
        if str(step["step_id"]) == str(sid1):
            db.update_step_state_machine(step_id=str(sid1), status="Success", outputs={"recovered": True})
            return {"success": True, "retryable": False, "blocked": False, "check_status": "unverified", "output": {"recovered": True}}
        return await original(current_plan, step)

    server._execute_step_with_state_machine = recovery
    await server.execute_plan_background(db.get_plan(str(pid)))
    final_plan = db.get_plan(str(pid))
    runs = db.list_execution_runs(str(pid))
    final_steps = db.get_steps(str(pid))
    events = db.get_step_events(str(pid))
    result = {
        "plan_id": str(pid),
        "first": {
            "plan_status": first_plan.status.value,
            "resume_from_step_id": str(first_plan.resume_from_step_id),
            "run_statuses": [r["status"] for r in first_runs],
            "step_statuses": [s["status"] for s in first_steps],
            "event_types": [e["event_type"] for e in first_events],
        },
        "second": {
            "plan_status": final_plan.status.value,
            "resume_from_step_id": str(final_plan.resume_from_step_id) if final_plan.resume_from_step_id else None,
            "run_statuses": [r["status"] for r in runs],
            "step_statuses": [s["status"] for s in final_steps],
            "event_types": [e["event_type"] for e in events],
            "artifact_exists": artifact.exists(),
            "artifact_content": artifact.read_text() if artifact.exists() else None,
            "calls": calls,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert first_plan.status.value == "Failed" and first_runs[0]["status"] == "Failed"
    assert final_plan.status.value == "Completed" and runs[0]["status"] == "Completed"
    assert all(s["status"] == "Success" for s in final_steps)
    assert artifact.read_text() == "checkpoint recovery accepted\n"
    Path("out/live-checkpoint-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    server._execute_step_with_state_machine = original


if __name__ == "__main__":
    asyncio.run(main())
