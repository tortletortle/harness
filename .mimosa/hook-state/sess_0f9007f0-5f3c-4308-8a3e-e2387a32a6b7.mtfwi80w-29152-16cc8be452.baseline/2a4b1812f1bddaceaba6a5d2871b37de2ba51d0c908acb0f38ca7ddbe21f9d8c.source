"""coding_agent 全流程真实 LLM E2E：

用户需求 → Designer 生成设计书（含 coding_agent 工具评估）→ 审批 →
生成含 coding_agent 步骤的执行计划 → 执行（Grant 受限）→ 确定性验收 → 审计关联。

与 test_exec_e2e.py 同构：全部走 /api/*，真实服务 + 真实模型。

运行方式（消耗真实 API 额度，默认跳过）：

    RUN_REAL_LLM_EVAL=1 python -m pytest tests/python/test_fullflow_e2e.py -q --no-cov
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

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
TOKEN = "fullflow-e2e-token-2026"
BASE = "http://127.0.0.1:8014"

RUN_REAL = os.environ.get("RUN_REAL_LLM_EVAL") == "1"
pytestmark = pytest.mark.skipif(not RUN_REAL, reason="需要 RUN_REAL_LLM_EVAL=1")


def _req(path, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data=data, timeout=60) as resp:
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
def server():
    import shutil

    root = Path(PYTHON_DIR.parent / ".tmp_fullflow_e2e_root")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "config").mkdir()
    (root / "data").mkdir()
    shutil.copy(PYTHON_DIR.parent / "config" / "models.yaml", root / "config" / "models.yaml")

    env = dict(os.environ)
    env["EAS_API_TOKEN"] = TOKEN
    # 工作区根必须显式绑定到隔离目录，否则相对路径会落到仓库根
    # （tools/workspace.py 的回退语义是仓库根，历史 E2E 用绝对路径从未暴露）。
    env["EAS_WORKSPACE_ROOT"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(PYTHON_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    log_path = root / "fullflow_server.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.server:app",
         "--host", "127.0.0.1", "--port", "8014"],
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
            pytest.fail("服务启动失败，详见 .tmp_fullflow_e2e_root/fullflow_server.log")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def executed_plan(server):
    """全流程：设计书 → 审批 → 计划 → 执行 → 完成。返回 (plan_id, plan_data)。"""
    req_body = {
        "user_request": (
            "使用以下工具完成四步，不要执行任何 shell 命令。必须严格按下面给出的顺序生成四个步骤，不得调整顺序："
            "第一步用 file_creator 创建 counter.py，内容为 def value():\\n    return 0；"
            "第二步用 file_creator 创建 test_counter.py，内容为 from counter import value\\n\\ndef test_value():\\n    assert value() == 1；"
            "第三步用 coding_agent 把 counter.py 里的 return 0 修改为 return 1，函数其余部分保持不变；"
            "第四步用 test_runner 运行 pytest 测试 test_counter.py 确认通过。"
            "每一步都给出可自动验证的 acceptance。"
        )
    }
    code, data = _req("/api/design", method="POST", body=req_body)
    assert code == 200, f"create design -> {code} {data!r}"
    design_id = data["design"]["design_id"]

    code, data = _req(
        f"/api/design/{design_id}/approve",
        method="POST",
        body={"design_id": design_id, "approved": True},
    )
    assert code == 200, f"approve design -> {code} {data!r}"
    plan = data.get("plan") or data.get("design", {}).get("plan")
    assert plan, f"approve response missing plan: {data!r}"
    plan_id = plan["plan_id"]

    # 计划里应出现 coding_agent 与 test_runner 步骤（设计期契约已允许并引导）
    code, data = _req(f"/api/plan/{plan_id}")
    assert code == 200, f"/api/plan/{plan_id} -> {code} {data!r}"
    steps = data.get("steps") or data.get("plan", {}).get("steps", [])
    agent_steps = [s for s in steps if s.get("tool") == "coding_agent"]
    assert agent_steps, f"计划未包含 coding_agent 步骤: {[s.get('tool') for s in steps]}"
    for s in agent_steps:
        assert s.get("acceptance"), f"coding_agent 步骤缺少 acceptance: {s!r}"
        assert s.get("inputs", {}).get("allowed_tools") == ["text_editor"], s.get("inputs")
    runner_steps = [s for s in steps if s.get("tool") == "test_runner"]
    assert runner_steps, f"计划未包含 test_runner 步骤: {[s.get('tool') for s in steps]}"
    for s in runner_steps:
        assert s.get("inputs", {}).get("suite"), f"test_runner 步骤缺少 suite: {s!r}"
        assert any(r.get("type") == "exit_code" for r in (s.get("acceptance") or [])), f"test_runner 步骤缺少 exit_code 验收: {s!r}"

    code, d = _req(f"/api/plan/{plan_id}/execute", method="POST")
    assert code == 200, f"execute -> {code} {d!r}"
    done = False
    for _ in range(120):
        code, st = _req("/api/status")
        if code == 200 and not st.get("current_execution"):
            done = True
            break
        time.sleep(1)
    assert done, "执行未在预期时间内结束"

    code, data = _req(f"/api/plan/{plan_id}")
    assert code == 200
    steps = data.get("steps") or data.get("plan", {}).get("steps", [])
    return plan_id, steps


def test_full_flow_plan_executes_all_success(executed_plan):
    plan_id, steps = executed_plan
    statuses = [s.get("status") for s in steps]
    assert statuses and all(s == "Success" for s in statuses), str(statuses)


def test_full_flow_agent_step_outputs_and_acceptance(executed_plan):
    _, steps = executed_plan
    agent_steps = [s for s in steps if s.get("tool") == "coding_agent"]
    assert agent_steps
    for s in agent_steps:
        outputs = s.get("outputs") or {}
        # 落库的 outputs 即 agent 结果字典：output 内层是 agent 负载。
        assert outputs.get("check_status") == "passed", str(outputs)[:500]
        acceptance = outputs.get("acceptance") or {}
        assert acceptance.get("status") == "passed"
        assert outputs.get("output", {}).get("agent_status") == "completed"
        facts = {f.get("key"): f.get("value") for f in outputs.get("agent_facts", [])}
        assert facts.get("coding_agent_file_exists") == "true"


def test_full_flow_final_file_content_via_agent_edit(executed_plan, tmp_path_factory):
    # 从工作区断言最终内容：server 的工作区即 .tmp_fullflow_e2e_root
    root = Path(PYTHON_DIR.parent / ".tmp_fullflow_e2e_root")
    counter = root / "counter.py"
    assert counter.exists(), f"missing {counter}"
    content = counter.read_text(encoding="utf-8")
    assert "return 1" in content
    assert "def value" in content


def test_full_flow_test_runner_closes_edit_test_loop(executed_plan):
    """改→测闭环：test_runner 步骤在 coding_agent 修改后运行且通过。"""
    _, steps = executed_plan
    runner_steps = [s for s in steps if s.get("tool") == "test_runner"]
    assert runner_steps
    for s in runner_steps:
        outputs = s.get("outputs") or {}
        # 普通工具步骤的 outputs 即工具结果字典（exit_code 在顶层）
        tool_output = outputs.get("output") if isinstance(outputs.get("output"), dict) else outputs
        assert tool_output.get("exit_code") == 0, str(outputs)[:500]
        assert tool_output.get("success") is True
        acceptance = outputs.get("acceptance") or {}
        if acceptance:
            assert acceptance.get("status") == "passed"


def test_full_flow_audit_links_agent_grant(executed_plan):
    plan_id, _ = executed_plan
    code, audit = _req(f"/api/audit-logs?limit=50&plan_id={plan_id}")
    assert code == 200, f"audit logs -> {code} {audit!r}"
    logs = audit.get("logs") or []
    assert logs, "audit logs 为空"
    assert all(log.get("grant_id") for log in logs)
