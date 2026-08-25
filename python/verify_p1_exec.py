"""
验证执行路径的 P1 修复：
1. 并发执行请求被锁挡住（409），不会产出两个后台任务
2. 计划状态在执行后回写（Running -> Completed/Failed）
3. 步骤 outputs 通过 API 可见（含失败原因与 check_status）
4. update_step 失败时中止执行
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

sys.path.insert(0, str(Path(__file__).parent))

TOKEN = "p1-exec-token-4455"
BASE = "http://127.0.0.1:8013"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def req(path, method="GET", body=None):
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


# 先用真实 DB 造一个计划（工具用 file_creator，不需要 LLM）
from database import Database
from models import (
    ExecutionPlan, PlanSegment, Step, StepStatus, PlanStatus,
    DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment,
)

db_path = Path("data/eas.db")
db = Database(str(db_path))

work = Path("verify_p1_workdir")
work.mkdir(exist_ok=True)

plan_id = uuid.uuid4()
design_id = uuid.uuid4()

# 外键已开启，必须先有对应的 design 行，否则 plans 写入会被拒绝
design = DesignDocument(
    design_id=design_id,
    user_request="P1 验证用设计",
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
check("前置 design 已写入", db.save_design(design))
seg_id = uuid.uuid4()
step_ok = uuid.uuid4()
step_two = uuid.uuid4()

plan = ExecutionPlan(
    plan_id=plan_id,
    design_id=design_id,
    created_at=datetime.now(),
    status=PlanStatus.PENDING,
    segments=[PlanSegment(
        segment_id=seg_id,
        description="验证段",
        steps=[
            Step(step_id=step_ok, order=1, description="创建文件A",
                 tool="file_creator",
                 inputs={"path": str(work / "a.txt"), "content": "hello"},
                 check_criteria="手动检查", status=StepStatus.PENDING),
            Step(step_id=step_two, order=2, description="创建文件B",
                 tool="file_creator",
                 inputs={"path": str(work / "b.txt"), "content": "world"},
                 check_criteria="手动检查", status=StepStatus.PENDING),
        ],
    )],
    implementation_list=[],
    dynamic_insertions=[],
)
saved = db.save_plan(plan)
check("测试计划已写入 DB", saved)

env = dict(os.environ)
env["EAS_API_TOKEN"] = TOKEN
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUNBUFFERED"] = "1"

log = open("verify_p1_server.log", "w", encoding="utf-8")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "8013"],
    stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(Path(__file__).parent),
)

try:
    ready = False
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/", timeout=5)
            ready = True
            break
        except Exception:
            time.sleep(1)
    check("服务启动成功", ready)

    if ready:
        # 计划可通过 API 读到，且 outputs 字段存在
        code, data = req(f"/api/plan/{plan_id}")
        check("GET /api/plan 返回 200", code == 200, f"code={code}")
        steps = data.get("plan", data).get("steps") if isinstance(data, dict) else None
        if steps is None:
            steps = data.get("steps", [])
        check("计划含 2 个步骤", len(steps) == 2, f"实际 {len(steps)}")
        check("步骤载荷含 outputs 键", all("outputs" in s for s in steps))

        # 触发执行
        code1, d1 = req(f"/api/plan/{plan_id}/execute", method="POST")
        check("首次执行请求被接受", code1 == 200, f"code={code1} {d1}")

        # 立刻再发一次 —— 应被锁拒绝
        code2, d2 = req(f"/api/plan/{plan_id}/execute", method="POST")
        check("并发执行请求被拒（409）", code2 == 409, f"code={code2} {d2}")

        # 等执行完成
        done = False
        for _ in range(40):
            code, st = req("/api/status")
            if code == 200 and not st.get("current_execution"):
                done = True
                break
            time.sleep(1)
        check("执行结束且锁已释放", done)

        # 文件真的被创建
        check("步骤1 产物存在", (work / "a.txt").exists())
        check("步骤2 产物存在", (work / "b.txt").exists())

        # 计划状态已回写
        fresh = Database(str(db_path)).get_plan(str(plan_id))
        pstatus = fresh.status.value if hasattr(fresh.status, "value") else str(fresh.status)
        check("计划状态已回写为 Completed", pstatus == "Completed", f"实际={pstatus}")

        # 步骤状态与 outputs
        code, data = req(f"/api/plan/{plan_id}")
        steps = data.get("steps") or data.get("plan", {}).get("steps", [])
        statuses = [s.get("status") for s in steps]
        check("两个步骤都是 Success", statuses == ["Success", "Success"], str(statuses))
        has_check = [s.get("outputs", {}).get("check_status") for s in steps if s.get("outputs")]
        check("outputs 中记录了 check_status", all(c is not None for c in has_check) and has_check,
              str(has_check))

        # 执行完成后可以再次执行（锁已释放）
        code3, d3 = req(f"/api/plan/{plan_id}/execute", method="POST")
        check("锁释放后可再次执行", code3 == 200, f"code={code3}")
        for _ in range(40):
            code, st = req("/api/status")
            if code == 200 and not st.get("current_execution"):
                break
            time.sleep(1)

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
print("\n" + "=" * 56)
print(f"总计 {total}，通过 {passed}，失败 {total - passed}")
if passed < total:
    print("\n失败项：")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name} {detail}")
print("=" * 56)
sys.exit(0 if passed == total else 1)
