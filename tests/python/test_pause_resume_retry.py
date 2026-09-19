"""
阶段3：可靠性——真实起服务验证暂停/恢复/跳过步骤 + 执行循环内重试。

使用临时目录中的数据库，不会污染生产数据库 data/eas.db。
参考 test_exec_e2e.py 的隔离服务模式。
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import pytest

TOKEN = "p3-reliability-token-9911"
BASE = "http://127.0.0.1:8014"
PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"


def _req(path, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data=data, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"err": str(e)}


@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("p3_workdir")


@pytest.fixture(scope="module")
def server_root(tmp_path_factory):
    import shutil

    root = tmp_path_factory.mktemp("p3_root")
    (root / "config").mkdir()
    shutil.copy(PYTHON_DIR.parent / "config" / "models.yaml", root / "config" / "models.yaml")
    (root / "data").mkdir()
    return root


def _new_plan_with_steps(db, workdir, steps_spec):
    """steps_spec: 每项为 (tool, inputs, check_criteria)"""
    from models import (
        ExecutionPlan, PlanSegment, Step, StepStatus, PlanStatus,
        DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment,
    )

    design_id = uuid.uuid4()
    design = DesignDocument(
        design_id=design_id,
        user_request="P3 验证用设计",
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

    steps = []
    for i, (tool, inputs, criteria) in enumerate(steps_spec):
        steps.append(Step(
            step_id=uuid.uuid4(), order=i + 1, description=f"步骤{i+1}",
            tool=tool, inputs=inputs, check_criteria=criteria,
            status=StepStatus.PENDING,
        ))

    plan_id = uuid.uuid4()
    plan = ExecutionPlan(
        plan_id=plan_id, design_id=design_id, created_at=datetime.now(),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(segment_id=uuid.uuid4(), description="seg", steps=steps)],
        implementation_list=[], dynamic_insertions=[],
    )
    assert db.save_plan(plan)
    return plan_id, [str(s.step_id) for s in steps]


@pytest.fixture(scope="module")
def plans_fixture(server_root, workdir):
    sys.path.insert(0, str(PYTHON_DIR))
    from database import Database

    db_path = server_root / "data" / "eas.db"
    db = Database(str(db_path))

    # 计划A：三个 file_creator 步骤，用于暂停/恢复/跳过验证
    plan_a_id, step_a_ids = _new_plan_with_steps(db, workdir, [
        ("file_creator", {"path": str(workdir / "a1.txt"), "content": "1"}, "手动检查"),
        ("file_creator", {"path": str(workdir / "a2.txt"), "content": "2"}, "手动检查"),
        ("file_creator", {"path": str(workdir / "a3.txt"), "content": "3"}, "手动检查"),
    ])

    # 计划B：一个必定失败的 manual 步骤，用于验证重试耗尽后计划标记为 Failed
    plan_b_id, step_b_ids = _new_plan_with_steps(db, workdir, [
        ("manual", {"description": "必定失败步骤", "error": "模拟错误"}, "手动检查"),
    ])

    return {
        "db_path": db_path,
        "plan_a_id": plan_a_id, "step_a_ids": step_a_ids,
        "plan_b_id": plan_b_id, "step_b_ids": step_b_ids,
    }


@pytest.fixture(scope="module")
def server(server_root, plans_fixture):
    env = dict(os.environ)
    env["EAS_API_TOKEN"] = TOKEN
    # 数据库必须显式绑定到隔离目录：服务端路径解析（paths.resolve_db_path）不再跟随 cwd，
    # 否则会打开仓库根的真实 data/eas.db，而计划是种在这里的临时库中。
    env["EAS_DB_PATH"] = str(server_root / "data" / "eas.db")
    env["EAS_LOG_DIR"] = str(server_root / "logs")  # 日志目录同理，不写进仓库根 logs/
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(PYTHON_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    log_path = server_root / "p3_server.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.server:app",
         "--host", "127.0.0.1", "--port", "8014"],
        stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(server_root),
    )

    ready = False
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/", timeout=5)
                ready = True
                break
            except Exception:
                time.sleep(1)

        if not ready:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
            pytest.fail("服务启动失败，详见 p3_server.log")

        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _wait_idle(timeout_s=40):
    for _ in range(timeout_s):
        code, st = _req("/api/status")
        if code == 200 and not st.get("current_execution"):
            return True
        time.sleep(1)
    return False


def test_skip_step_marks_skipped_and_is_not_executed(server, plans_fixture, workdir):
    plan_id = plans_fixture["plan_a_id"]
    step_ids = plans_fixture["step_a_ids"]

    # 跳过第二步
    code, data = _req(f"/api/plan/{plan_id}/skip-step/{step_ids[1]}", method="POST")
    assert code == 200, f"code={code} {data}"

    code, data = _req(f"/api/plan/{plan_id}")
    steps = data.get("plan", data).get("steps") if isinstance(data.get("plan"), dict) else data.get("steps", [])
    by_id = {s["step_id"]: s for s in steps}
    assert by_id[step_ids[1]]["status"] == "Skipped"

    # 执行整个计划：跳过的步骤不应被执行（对应文件不应被创建）
    code, data = _req(f"/api/plan/{plan_id}/execute", method="POST")
    assert code == 200, f"code={code} {data}"
    assert _wait_idle(), "执行未在预期时间内结束"

    assert (workdir / "a1.txt").exists()
    assert not (workdir / "a2.txt").exists(), "被跳过的步骤不应产出文件"
    assert (workdir / "a3.txt").exists()

    from database import Database
    fresh = Database(str(plans_fixture["db_path"])).get_plan(str(plan_id))
    status = fresh.status.value if hasattr(fresh.status, "value") else fresh.status
    assert status == "Completed", f"实际={status}"


def test_pause_requires_running_plan(server, plans_fixture):
    plan_id = plans_fixture["plan_a_id"]
    # 计划A此时已是 Completed（上一个测试跑完了），暂停应该被拒绝
    code, data = _req(f"/api/plan/{plan_id}/pause", method="POST")
    assert code == 409, f"code={code} {data}"


def test_manual_failure_is_not_auto_repeated_and_plan_fails(server, plans_fixture):
    """human_tool 步骤失败不可自动重试：只尝试一次即 Failed，计划随之 Failed。

    自动重试只对「确定性 + 无副作用 + 幂等」的工具开放（见 python/retry_policy.py）。
    manual 是人工确认步骤，重放会重复请求人工动作，因此要求显式重新执行。
    """
    plan_id = plans_fixture["plan_b_id"]

    code, data = _req(f"/api/plan/{plan_id}/execute", method="POST")
    assert code == 200, f"code={code} {data}"
    assert _wait_idle(timeout_s=60), "执行未在预期时间内结束"

    from database import Database
    fresh = Database(str(plans_fixture["db_path"])).get_plan(str(plan_id))
    status = fresh.status.value if hasattr(fresh.status, "value") else fresh.status
    assert status == "Failed", f"实际={status}"

    step = fresh.segments[0].steps[0]
    assert step.retry_count == 0, (
        f"人工步骤不应被自动重放: retry_count={step.retry_count}"
    )
    outputs = step.outputs or {}
    assert outputs.get("retry_policy", {}).get("eligible") is False, outputs
    assert outputs.get("manual_retry_required") is True, outputs


def test_resume_continues_from_first_incomplete_step(server, plans_fixture, workdir):
    """
    模拟一个已经执行到一半后被暂停的计划：第一步已 Success，计划状态为 Paused。
    调用 /resume 应该跳过已完成的第一步，只执行剩余步骤，最终计划变为 Completed。
    """
    sys.path.insert(0, str(PYTHON_DIR))
    from database import Database

    db = Database(str(plans_fixture["db_path"]))
    plan_id, step_ids = _new_plan_with_steps(db, workdir, [
        ("file_creator", {"path": str(workdir / "r1.txt"), "content": "1"}, "手动检查"),
        ("file_creator", {"path": str(workdir / "r2.txt"), "content": "2"}, "手动检查"),
    ])

    # 模拟"第一步已完成，计划被暂停"的中间状态
    assert db.update_step(step_ids[0], "Success", outputs={"ok": True})
    assert db.update_plan(str(plan_id), "Paused")

    code, data = _req(f"/api/plan/{plan_id}/resume", method="POST")
    assert code == 200, f"code={code} {data}"
    assert _wait_idle(), "恢复执行未在预期时间内结束"

    assert (workdir / "r2.txt").exists()

    fresh = db.get_plan(str(plan_id))
    status = fresh.status.value if hasattr(fresh.status, "value") else fresh.status
    assert status == "Completed", f"实际={status}"


def test_resume_rejected_when_plan_not_paused(server, plans_fixture):
    plan_id = plans_fixture["plan_a_id"]
    # 计划A此时已是 Completed，恢复应该被拒绝
    code, data = _req(f"/api/plan/{plan_id}/resume", method="POST")
    assert code == 409, f"code={code} {data}"
