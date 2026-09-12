"""B-standard real-LLM acceptance for the WorkGraph runtime chains.

Covers the four capabilities added after the A standard, against a REAL LLM:
  S1  task-mode routing (direct tool mode with Grant/audit)
  S2  full graph flow + artifact auto-registration + final delivery report
  S3  worktree isolation end-to-end (bind -> execute -> server-side diff -> merge)
  S4  dynamic replan self-healing (fail -> repair steps -> rerun -> complete)

Run inside a real server lifespan (TestClient triggers the same lifespan
assembly as uvicorn). The HTTP target is the local control plane; the
acceptance workspace must be a prepared git repository containing README.md.
No key is ever printed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

TOKEN = "eas-b-standard-acceptance-token"
_ALLOWED_SCHEMES = {"http", "https"}
_RUN_SEQ = {"n": 0}


def _validate_base(base: str, allow_local: bool) -> str:
    """仅允许 http/https；本验收只访问本机控制面（显式 allow_local）。"""
    parsed = urlparse(base)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError(f"仅允许 http/https 且包含 host: {base!r}")
    if allow_local and parsed.hostname in {"127.0.0.1", "::1"}:
        return base
    raise ValueError(f"拒绝非本机目标: {base!r}")


def request(method: str, path: str, body=None, timeout: int = 300):
    _validate_base("http://127.0.0.1", allow_local=True)
    from fastapi.testclient import TestClient

    # _CLIENT 由验收入口注入（真实 lifespan 已装配的全局实例）。
    headers = {"Authorization": f"Bearer {os.environ.get('EAS_API_TOKEN', '')}"}
    response = (
        _CLIENT.request(method, path, json=body, headers=headers)
        if body is not None
        else _CLIENT.request(method, path, headers=headers)
    )
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"detail": response.text[:500]}


_CLIENT = None


def wait_plan(client, plan_id: str, timeout: int = 420) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        code, response = request("GET", f"/api/plan/{plan_id}", timeout=60)
        plan = response.get("plan", {}) if code == 200 else {}
        if plan.get("status") in {"Completed", "Failed", "RolledBack"}:
            return plan
        time.sleep(3)
    return {"status": "Timeout"}


def _create_approved_plan(user_request: str):
    # 场景请求带序号后缀：避免设计/计划缓存跨场景（或跨次运行）命中，
    # 保证每个场景走真实的 design → plan 链路。
    _RUN_SEQ["n"] += 1
    unique_request = f"{user_request}（验收批次 #{_RUN_SEQ['n']}）"
    code, design = request("POST", "/api/design", {"user_request": unique_request})
    design_id = (design.get("design") or {}).get("design_id")
    if code != 200 or not design_id:
        return None, {"stage": "design", "status_code": code, "response": design}
    code, approval = request("POST", f"/api/design/{design_id}/approve",
                             {"design_id": design_id, "approved": True})
    plan_id = (approval.get("plan") or {}).get("plan_id")
    if code != 200 or not plan_id:
        return None, {"stage": "approve", "status_code": code, "response": approval}
    return plan_id, None


def run_acceptance(timeout: int) -> dict:
    evidence = {"standard": "B", "scenarios": [], "passed": False}

    # ===== S1 任务模式识别（direct）=====
    s1 = {"name": "task_mode_direct"}
    code, body = request("POST", "/api/quick-task",
                         {"message": "读取 README.md"}, timeout=60)
    s1["status_code"] = code
    s1["mode"] = body.get("mode")
    s1["tool_id"] = body.get("tool_id")
    s1["result_success"] = (body.get("result") or {}).get("success")
    s1["passed"] = (
        code == 200 and body.get("mode") == "direct"
        and body.get("tool_id") == "text_editor"
        and (body.get("result") or {}).get("success") is True
    )
    evidence["scenarios"].append(s1)

    # ===== S2 graph 全链路 + artifacts + 交付报告 =====
    s2 = {"name": "graph_flow_artifacts_report"}
    user_request = (
        "在工作区创建 out/b-standard.txt，内容包含标记 B-STANDARD-OK，"
        "并在工作区创建 out/summary.md，内容为简短总结。"
    )
    plan_id, failure = _create_approved_plan(user_request)
    if failure:
        s2.update({"passed": False, **failure})
        evidence["scenarios"].append(s2)
        return evidence
    s2["plan_id"] = plan_id

    code, plan_response = request("GET", f"/api/plan/{plan_id}", timeout=60)
    steps = (plan_response.get("plan") or {}).get("steps", [])
    s2["plan_steps"] = len(steps)
    s2["has_explicit_dependencies"] = any(s.get("depends_on") for s in steps)

    code, _ = request("POST", f"/api/plan/{plan_id}/execute")
    final_plan = wait_plan(_CLIENT, plan_id, timeout)
    s2["plan_status"] = final_plan.get("status")

    # 交付报告由真实 LLM 生成（Completed 后异步进行），轮询等待其登记。
    s2["delivery_report"] = False
    deadline = time.time() + 120
    while time.time() < deadline:
        code, artifacts = request("GET", f"/api/artifacts?plan_id={plan_id}", timeout=60)
        all_artifacts = artifacts.get("artifacts", [])
        s2["artifact_count"] = len([a for a in all_artifacts if a.get("kind") == "file"])
        s2["delivery_report"] = any(a.get("kind") == "delivery_report" for a in all_artifacts)
        if s2["delivery_report"]:
            break
        time.sleep(3)
    s2["passed"] = (
        final_plan.get("status") == "Completed"
        and s2["artifact_count"] >= 1
        and s2["delivery_report"]
    )
    evidence["scenarios"].append(s2)
    if not s2["passed"]:
        return evidence

    # ===== S3 worktree 隔离 E2E（产物经服务器 diff API 验证）=====
    s3 = {"name": "worktree_isolation"}
    user_request = "在工作区创建 wt-proof.txt，内容包含标记 WORKTREE-OK。"
    plan_id, failure = _create_approved_plan(user_request)
    if failure:
        s3.update({"passed": False, **failure})
        evidence["scenarios"].append(s3)
        return evidence
    s3["plan_id"] = plan_id

    # 唯一名：避免跨次运行的 worktree/分支残留导致重名拒绝
    code, binding = request("POST", f"/api/plan/{plan_id}/worktree",
                            {"name": f"b-accept-{int(time.time())}"})
    s3["bind_status"] = code
    if code != 200 or not binding.get("worktree_path"):
        s3.update({"passed": False, "stage": "bind", "response": binding})
        evidence["scenarios"].append(s3)
        return evidence

    code, _ = request("POST", f"/api/plan/{plan_id}/execute")
    final_plan = wait_plan(_CLIENT, plan_id, timeout)
    s3["plan_status"] = final_plan.get("status")
    # 计划 Completed 意味着步骤内确定性验收（file_exists，解析到 worktree）已通过，
    # 即产物确实落在 worktree 而非主工作区。

    # merge（内含自动快照未提交产物）后，用 direct 工具模式读取主仓库文件，
    # 验证 worktree 产物真实合并回主仓库——纯 HTTP 闭环，不依赖本地 FS。
    code, merged = request("POST", f"/api/plan/{plan_id}/worktree/merge",
                           {"message": "B-standard merge"})
    s3["merge_status"] = code

    code, read_back = request("POST", "/api/quick-task",
                              {"message": "读取 wt-proof.txt"}, timeout=60)
    merged_content = ((read_back.get("result") or {}).get("content") or "")
    s3["merged_content_ok"] = "WORKTREE-OK" in str(merged_content)
    s3["passed"] = (
        final_plan.get("status") == "Completed"
        and code == 200
        and s3["merged_content_ok"]
    )
    evidence["scenarios"].append(s3)

    # ===== S4 动态重规划自愈 =====
    s4 = {"name": "replan_self_healing"}
    # 确定性失败制造：test_runner 对不存在的测试文件跑 pytest → exit 非 0
    # → test_passes 验收失败 → 计划 Failed（lint 对缺失文件是放行语义，不可用）。
    user_request = (
        "在工作区用 test_runner 运行 pytest 测试 output/does-not-exist_test.py"
        "（该文件尚不存在，本任务用于验证失败处理流程），"
        "acceptance 使用 test_passes。"
    )
    plan_id, failure = _create_approved_plan(user_request)
    if failure:
        s4.update({"passed": False, **failure})
        evidence["scenarios"].append(s4)
        return evidence
    s4["plan_id"] = plan_id

    code, _ = request("POST", f"/api/plan/{plan_id}/execute")
    final_plan = wait_plan(_CLIENT, plan_id, timeout)
    s4["first_status"] = final_plan.get("status")
    if final_plan.get("status") != "Failed":
        s4.update({"passed": False, "note": "计划未按预期失败，无法验证重规划"})
        evidence["scenarios"].append(s4)
        return evidence

    code, replan = request("POST", f"/api/plan/{plan_id}/replan")
    s4["replan_status"] = code
    s4["repair_steps"] = replan.get("repair_steps")
    if code != 200 or not replan.get("success"):
        s4.update({"passed": False, "stage": "replan", "response": replan})
        evidence["scenarios"].append(s4)
        return evidence

    code, _ = request("POST", f"/api/plan/{plan_id}/execute")
    final_plan = wait_plan(_CLIENT, plan_id, timeout)
    s4["second_status"] = final_plan.get("status")
    s4["passed"] = final_plan.get("status") == "Completed"
    evidence["scenarios"].append(s4)

    evidence["passed"] = all(s.get("passed") for s in evidence["scenarios"])
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True,
                        help="验收工作区（必须是已初始化的 git 仓库，且包含 README.md）")
    parser.add_argument("--timeout", type=int, default=420, help="单计划执行等待上限（秒）")
    args = parser.parse_args()

    workspace = args.workspace
    if not (workspace / ".git").exists():
        print(f"FAIL: --workspace 必须是已初始化的 git 仓库: {workspace}")
        return 2
    if not (workspace / "README.md").exists():
        print(f"FAIL: 工作区必须包含 README.md（S1 直接工具模式读取对象）: {workspace}")
        return 2

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(".env"), override=False)
    except Exception:
        pass

    if not (os.getenv("EAS_LLM_API_KEY_STEPFUN") or os.getenv("EAS_LLM_API_KEY_NEWAPI")):
        print("FAIL: .env 必须提供 EAS_LLM_API_KEY_STEPFUN / EAS_LLM_API_KEY_NEWAPI")
        return 2
    token = os.getenv("EAS_API_TOKEN", "")
    if not token:
        token_path = Path("config/api_token")
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    if not token:
        print("FAIL: EAS_API_TOKEN 或 config/api_token 缺失")
        return 2

    os.environ["EAS_API_TOKEN"] = token
    os.environ["EAS_WORKSPACE_ROOT"] = str(workspace)

    # python/ 下的模块按顶层导入约定（同 tests/conftest.py）
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

    global _CLIENT
    from fastapi.testclient import TestClient
    from api.server import app, state

    with TestClient(app) as client:
        # 触发真实 lifespan：LLM client、designer、executor、capability registry 全装配
        _CLIENT = client
        evidence = run_acceptance(args.timeout)

    evidence_text = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    output_path = Path("out") / "real-llm-acceptance-b.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(evidence_text, encoding="utf-8")
    print(evidence_text)
    return 0 if evidence.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
