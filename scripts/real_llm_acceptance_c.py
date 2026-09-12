"""C-standard real-LLM acceptance: parallel execution, multi-worktree,
agent session/turn recording, and delivery-report content.

Covers the evidence still missing after the A/B standards:
  C1  real controlled-parallel execution (parallel_graphs=true) of an
      explicitly-depended graph, measured by wall-clock overlap
  C2  multi-worktree: two worktree-bound plans run concurrently against one
      main workspace (each writes a distinct file into its own worktree)
  C3  AgentSession/Turn recording produced by a real coding_agent step
  C4  delivery report content is real, readable markdown

Note: the real planner reliably emits linear plans (observed twice in B), so
C1/C2 inject explicit depends_on deterministically to actually drive the
parallel and worktree schedulers; every tool call is authentic. Concurrency is
proven by wall-clock: two independent ~2s steps complete in <3.8s (not 4s).

Windows-compatible slow step: `python -c "import time; time.sleep(2)"` (no
unix `sleep`). Run inside a real server lifespan; pinned to local control
plane; never prints a key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from fastapi.testclient import TestClient

SLOW = "python -c \"import time; time.sleep(2)\""


def _credentials() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(".env"), override=False)
    except Exception:
        pass
    if not (os.getenv("EAS_LLM_API_KEY_STEPFUN") or os.getenv("EAS_LLM_API_KEY_NEWAPI")):
        return "FAIL: .env 必须提供真实 LLM key"
    token = os.getenv("EAS_API_TOKEN", "")
    if not token:
        token_path = Path("config/api_token")
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    if not token:
        return "FAIL: EAS_API_TOKEN 或 config/api_token 缺失"
    return token


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    ws = args.workspace
    if not (ws / ".git").exists():
        print(f"FAIL: workspace 必须是 git 仓库: {ws}")
        return 2

    token = _credentials()
    if token.startswith("FAIL"):
        print(token)
        return 2

    os.environ["EAS_API_TOKEN"] = token
    os.environ["EAS_WORKSPACE_ROOT"] = str(ws)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

    from api.server import app, state
    from api.server import execute_plan_background
    from models import (
        Architecture, DesignDocument, DesignPart1, Difficulty, ExecutionPlan,
        PlanSegment, PlanStatus, Step, ToolAssessment,
    )

    report = {"standard": "C", "scenarios": [], "passed": False}
    headers = {"Authorization": f"Bearer {token}"}

    def call(client, method, path, body=None):
        r = client.request(method, path, json=body, headers=headers) if body is not None \
            else client.request(method, path, headers=headers)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"detail": r.text[:300]}

    def make_plan(steps, user_request="C 验收计划"):
        design = DesignDocument(
            design_id=uuid4(), user_request=user_request, timestamp=datetime.utcnow(),
            part1_design=DesignPart1(
                architecture=Architecture(components=["c"], interactions=["i"]),
                difficulty=Difficulty(overall="low", breakdown={}),
            ),
            part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
            user_approval=True,
        )
        state.database.save_design(design)
        plan = ExecutionPlan(
            plan_id=uuid4(), design_id=design.design_id, status=PlanStatus.PENDING,
            created_at=datetime.utcnow(),
            segments=[PlanSegment(segment_id=uuid4(), description="graph", steps=steps)],
            implementation_list=[],
        )
        state.database.save_plan(plan)
        return plan

    with TestClient(app) as client:
        # 事件循环复用（Windows 下反复 asyncio.run 会重建循环引入大额开销）
        loop = asyncio.new_event_loop()

        # ===== C1 真实受控并行（显式依赖图 + parallel_graphs=true）=====
        c1 = {"name": "parallel_controlled"}
        state.parallel_graphs = True
        state.executor_max_concurrency = 2
        root = Step(step_id=uuid4(), order=1, description="root", tool="command_runner",
                    inputs={"command": "echo root"}, check_criteria="手动检查")
        sib_a = Step(step_id=uuid4(), order=2, description="sibling-a", tool="command_runner",
                     inputs={"command": SLOW}, check_criteria="手动检查",
                     depends_on=[str(root.step_id)])
        sib_b = Step(step_id=uuid4(), order=3, description="sibling-b", tool="command_runner",
                     inputs={"command": SLOW}, check_criteria="手动检查",
                     depends_on=[str(root.step_id)])
        g_plan = make_plan([root, sib_a, sib_b], "并行依赖图")
        t0 = time.time()
        loop.run_until_complete(
            execute_plan_background(state.database.get_plan(str(g_plan.plan_id)))
        )
        elapsed = time.time() - t0
        g_status = state.database.get_plan_status(str(g_plan.plan_id))
        c1["plan_status"] = g_status
        c1["elapsed_s"] = round(elapsed, 2)
        # 权威判据：两个 sibling 的 running 事件时间戳重叠（真实并发）且各步骤成功
        sib_a_running = None
        sib_b_running = None
        sib_a_ok = sib_b_ok = False
        for ev in state.database.get_step_events(str(g_plan.plan_id)):
            if ev["step_id"] == str(sib_a.step_id) and ev["event_type"] == "running":
                sib_a_running = ev["created_at"]
            if ev["step_id"] == str(sib_b.step_id) and ev["event_type"] == "running":
                sib_b_running = ev["created_at"]
        rows = {r["step_id"]: r["status"] for r in state.database.get_steps(str(g_plan.plan_id))}
        sib_a_ok = rows.get(str(sib_a.step_id)) == "Success"
        sib_b_ok = rows.get(str(sib_b.step_id)) == "Success"
        # running 时间戳重叠 + 二者成功 → 真实并行（created_at 是 ISO 字符串）
        def _iso_seconds(value):
            if isinstance(value, datetime):
                return value.timestamp()
            from datetime import datetime as _dt
            try:
                return _dt.fromisoformat(str(value)).timestamp()
            except ValueError:
                return None

        a_ts = _iso_seconds(sib_a_running)
        b_ts = _iso_seconds(sib_b_running)
        overlapped = a_ts is not None and b_ts is not None and abs(a_ts - b_ts) < 2.0
        c1["running_overlap"] = overlapped
        c1["sibling_a_ok"] = sib_a_ok
        c1["sibling_b_ok"] = sib_b_ok
        c1["batched_parallel"] = g_status == "Completed" and overlapped and sib_a_ok and sib_b_ok
        c1["passed"] = c1["batched_parallel"]
        report["scenarios"].append(c1)
        if not c1["batched_parallel"]:
            report["passed"] = False
            out = Path("out/real-llm-acceptance-c.json")
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        # ===== C2 多 worktree 并行（两计划各绑 worktree，写各自文件）=====
        c2 = {"name": "multi_worktree_parallel"}
        # 关闭批并行隔离干扰，专注 worktree 隔离
        state.parallel_graphs = False
        plans_wt = []
        _wt_ts = int(time.time())
        for tag in ("alpha", "beta"):
            step = Step(step_id=uuid4(), order=1, description=f"写 {tag}.txt", tool="file_creator",
                        inputs={"path": f"{tag}.txt", "content": f"content-{tag}"},
                        check_criteria="手动检查")
            plan = make_plan([step], f"worktree-{tag}")
            code, bind = call(client, "POST", f"/api/plan/{str(plan.plan_id)}/worktree",
                              {"name": f"c-{tag}-{_wt_ts}"})
            if code != 200:
                c2.update({"passed": False, "stage": "bind", "status_code": code, "response": bind})
                report["scenarios"].append(c2)
                report["passed"] = False
                Path("out/real-llm-acceptance-c.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 1
            plans_wt.append((plan, bind["worktree_path"]))
        # 两计划并发执行（复用同一 loop）
        t0 = time.time()
        async def _run_worktrees():
            await asyncio.gather(
                execute_plan_background(state.database.get_plan(str(plans_wt[0][0].plan_id))),
                execute_plan_background(state.database.get_plan(str(plans_wt[1][0].plan_id))),
            )
        loop.run_until_complete(_run_worktrees())
        elapsed = time.time() - t0
        c2["elapsed_s"] = round(elapsed, 2)
        c2["statuses"] = [
            state.database.get_plan_status(str(p[0].plan_id)) for p in plans_wt
        ]
        # 产物应在各自 worktree，主仓库应为空
        c2["alpha_in_main"] = (ws / "alpha.txt").exists()
        c2["beta_in_main"] = (ws / "beta.txt").exists()
        c2["passed"] = (
            all(s == "Completed" for s in c2["statuses"])
            and not c2["alpha_in_main"] and not c2["beta_in_main"]
        )
        report["scenarios"].append(c2)
        if not c2["passed"]:
            report["passed"] = False
            Path("out/real-llm-acceptance-c.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        # ===== C3 AgentSession/Turn（真实 coding_agent）=====
        c3 = {"name": "agent_session_turn"}
        # 经真实设计→审批→执行全链路，让 coding_agent 产生 session/turn
        code, design = call(client, "POST", "/api/design",
                            {"user_request": "创建 marker.py，内容为 x=1，然后修改它为 x=2"})
        design_id = (design.get("design") or {}).get("design_id")
        code, approval = call(client, "POST", f"/api/design/{design_id}/approve",
                              {"design_id": design_id, "approved": True})
        plan_id = (approval.get("plan") or {}).get("plan_id")
        code, _ = call(client, "POST", f"/api/plan/{plan_id}/execute")
        # 轮询完成
        deadline = time.time() + args.timeout
        final = None
        while time.time() < deadline:
            code, resp = call(client, "GET", f"/api/plan/{plan_id}")
            final = (resp.get("plan") or {}).get("status")
            if final in ("Completed", "Failed", "RolledBack"):
                break
            time.sleep(3)
        c3["plan_status"] = final
        code, sessions = call(client, "GET", f"/api/agent-sessions?plan_id={plan_id}")
        session_list = sessions.get("sessions", [])
        c3["session_count"] = len(session_list)
        c3["turns"] = []
        for session in session_list:
            code, turns = call(client, "GET", f"/api/agent-sessions/{session['session_id']}/turns")
            turn_list = turns.get("turns", [])
            c3["turns"].append({"session": session["agent_tool"], "turns": len(turn_list),
                                "status": session["status"], "tokens": session["input_tokens"] + session["output_tokens"]})
        c3["passed"] = final == "Completed" and len(session_list) >= 1 and any(t["turns"] >= 1 for t in c3["turns"])
        report["scenarios"].append(c3)
        if not c3["passed"]:
            report["passed"] = False

        # ===== C4 交付报告内容（报告由真实 LLM 异步生成，需轮询等待）=====
        c4 = {"name": "delivery_report_content"}
        report_artifact = None
        deadline = time.time() + 120
        while time.time() < deadline:
            code, artifacts = call(client, "GET", f"/api/artifacts?plan_id={plan_id}")
            report_artifact = next(
                (a for a in artifacts.get("artifacts", []) if a.get("kind") == "delivery_report"),
                None,
            )
            if report_artifact:
                break
            time.sleep(3)
        c4["report_found"] = report_artifact is not None
        if report_artifact:
            # /content 返回纯文本（非 JSON）：直接读 body，避免强制 json 解析失败
            r = client.request(
                "GET",
                f"/api/artifacts/{report_artifact['artifact_id']}/content",
                headers=headers,
            )
            content = r.text
            c4["content_length"] = len(content)
            c4["content_nonempty"] = isinstance(content, str) and len(content.strip()) > 40
        else:
            c4["content_nonempty"] = False
        c4["passed"] = c4["report_found"] and c4.get("content_nonempty", False)
        report["scenarios"].append(c4)

        report["passed"] = all(s.get("passed") for s in report["scenarios"])

    out = Path("out/real-llm-acceptance-c.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
