"""
执行路径端到端验证（纯 API）：

1. 通过 API 创建设计书
2. 批准设计书 → 生成执行计划
3. 执行计划
4. 验证执行结果（文件创建、状态回写、并发锁）

全部走 /api/*，不再直接写数据库，彻底避开 test/server DB 路径不一致问题。
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# This is intentionally a real-model integration test, never part of offline pytest.
pytestmark = pytest.mark.integration

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
TOKEN = "p1-exec-token-4455"
BASE = "http://127.0.0.1:8013"


def _req(path, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data=data, timeout=180) as resp:
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
    return tmp_path_factory.mktemp("verify_p1_workdir")


@pytest.fixture(scope="module")
def server(request, tmp_path_factory):
    """
    启动真实服务，走最小隔离 cwd，但不再依赖测试手写 DB。
    通过 API 创建设计书/计划，避免 DB 路径不一致问题。
    """
    import shutil

    if not request.config.getoption("--live-models"):
        pytest.skip("Real-model subprocess requires explicit --live-models")
    root = tmp_path_factory.mktemp("exec_e2e_live_server")
    (root / "config").mkdir()
    (root / "data").mkdir()
    shutil.copy(PYTHON_DIR.parent / "config" / "models.yaml", root / "config" / "models.yaml")

    env = dict(os.environ)
    env["EAS_API_TOKEN"] = TOKEN
    # 数据库必须显式绑定到隔离目录：服务端路径解析（paths.resolve_db_path）不再跟随 cwd，
    # 否则通过 API 创建的设计书/计划会写进仓库根的真实 data/eas.db。
    env["EAS_DB_PATH"] = str(root / "data" / "eas.db")
    env["EAS_LOG_DIR"] = str(root / "logs")  # 日志目录同理，不写进仓库根 logs/
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(PYTHON_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    log_path = root / "verify_p1_server.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.server:app",
         "--host", "127.0.0.1", "--port", "8013"],
        stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(root),
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
            pytest.fail("服务启动失败，详见 .tmp_exec_e2e_root/verify_p1_server.log")

        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def plan_fixture(server, workdir):
    """
    通过 API 创建设计书并批准，拿到可执行的 plan_id。
    这样 plan 一定写在 server 当前使用的 DB 里。
    """
    req_body = {
        "user_request": f"只使用 file_creator 工具，不要执行任何 shell 命令。在工作目录创建两个文件：{workdir / 'a.txt'} 内容 hello，{workdir / 'b.txt'} 内容 world"
    }
    code, data = _req("/api/design", method="POST", body=req_body)
    assert code == 200, f"create design -> {code} {data!r}"
    design_id = data["design"]["design_id"]

    code, data = _req(f"/api/design/{design_id}/approve", method="POST", body={"design_id": design_id, "approved": True})
    assert code == 200, f"approve design -> {code} {data!r}"
    plan = data.get("plan") or data.get("design", {}).get("plan")
    assert plan, f"approve response missing plan: {data!r}"
    plan_id = plan["plan_id"]

    return {"plan_id": plan_id, "workdir": workdir}


def test_plan_readable_via_api_with_outputs_field(server, plan_fixture):
    plan_id = plan_fixture["plan_id"]
    code, data = _req(f"/api/plan/{plan_id}")
    assert code == 200, f"/api/plan/{plan_id} -> {code} {data!r}"

    steps = data.get("steps") or data.get("plan", {}).get("steps", [])
    file_steps = [s for s in steps if s.get("tool") == "file_creator"]
    # 真实 LLM 偶尔会附加额外步骤；断言放宽为“至少两个 file_creator 步骤”
    assert len(file_steps) >= 2, f"file_creator steps: {file_steps}"
    assert all("outputs" in s for s in file_steps)


def test_concurrent_execution_and_full_run(server, plan_fixture, workdir):
    plan_id = plan_fixture["plan_id"]

    # 触发执行
    code1, d1 = _req(f"/api/plan/{plan_id}/execute", method="POST")
    assert code1 == 200, f"execute -> {code1} {d1!r}"

    # 立刻再发一次 —— 应被锁拒绝
    code2, d2 = _req(f"/api/plan/{plan_id}/execute", method="POST")
    assert code2 == 409, f"concurrent lock -> {code2} {d2!r}"

    # 等执行完成
    done = False
    for _ in range(60):
        code, st = _req("/api/status")
        if code == 200 and not st.get("current_execution"):
            done = True
            break
        time.sleep(1)
    assert done, "执行未在预期时间内结束或锁未释放"

    # 文件真的被创建
    code, data = _req(f"/api/plan/{plan_id}")
    print("[DEBUG] plan after execute:", json.dumps(data, ensure_ascii=False, indent=2)[:4000])
    assert (workdir / "a.txt").exists(), f"missing a.txt under {workdir}"
    assert (workdir / "b.txt").exists(), f"missing b.txt under {workdir}"

    # 计划状态与步骤 outputs 通过 API 读取，避免直接读 DB 路径不一致
    code, data = _req(f"/api/plan/{plan_id}")
    assert code == 200, f"/api/plan/{plan_id} -> {code} {data!r}"
    steps = data.get("steps") or data.get("plan", {}).get("steps", [])
    statuses = [s.get("status") for s in steps]
    assert statuses and all(s == "Success" for s in statuses), str(statuses)
    has_check = [s.get("outputs", {}).get("check_status") for s in steps if s.get("outputs")]
    assert has_check and all(c is not None for c in has_check), str(has_check)

    # 确认真实 LLM 生成的结构化验收被执行并留下确定性证据，
    # 不只是工具自报 success。
    acceptance_results = [
        s.get("outputs", {}).get("acceptance")
        for s in steps
        if s.get("outputs")
    ]
    assert acceptance_results and all(r and r.get("status") == "passed" for r in acceptance_results)
    assert all(
        all(check.get("passed") for check in r.get("checks", []))
        for r in acceptance_results
    )

    # 每次实际工具调用都应关联 Runtime 临时 Grant，审计只暴露 grant_id
    # 与用量摘要，不暴露授权本体。
    code, audit = _req(f"/api/audit-logs?limit=20&plan_id={plan_id}")
    assert code == 200, f"audit logs -> {code} {audit!r}"
    assert audit.get("logs") and all(log.get("grant_id") for log in audit["logs"])

    # 执行完成后可以再次执行（锁已释放）
    code3, d3 = _req(f"/api/plan/{plan_id}/execute", method="POST")
    assert code3 == 200, f"reexecute -> {code3} {d3!r}"
    for _ in range(40):
        code, st = _req("/api/status")
        if code == 200 and not st.get("current_execution"):
            break
        time.sleep(1)
