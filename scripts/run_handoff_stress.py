"""Offline full API + real Agent Tool handoff, input, and resume race.

Creates a private TestClient lifespan under a new temporary DB and workspace.
The Agent's model response is deterministic, but the Grant, executor, scheduler,
HTTP routes, persistence, and delivery report are the real application code.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()  # guarded app changes cwd to the isolated root
    root = Path(tempfile.mkdtemp(prefix="arena-eas-stress-"))
    token = "arena-stress-" + secrets.token_urlsafe(22)
    os.environ["ARENA_STRESS_ROOT"] = str(root)
    os.environ["ARENA_STRESS_MODE"] = "offline"
    os.environ["ARENA_STRESS_MODEL_LIMIT"] = "0"
    os.environ["EAS_API_TOKEN"] = token
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.stress_guarded_app import app  # import only after safety env exists
    from api.shared import state

    report = {"schema": "isolated-agent-handoff/v1", "success": False,
              "isolated_db": True, "external_mcp_disabled": True}
    headers = {"Authorization": f"Bearer {token}"}
    timings = {}

    def call(client, method, path, data=None):
        start = time.perf_counter()
        resp = client.request(method, path, headers=headers, json=data)
        timings.setdefault(method + " " + path.split("/")[2], []).append(
            round((time.perf_counter() - start) * 1000, 2))
        return resp.status_code, resp.json() if resp.content else {}

    def fail(stage, **details):
        report.update(stage=stage, **details)
        return 1

    try:
        with TestClient(app) as client:
            # An untrusted request to the isolated test service must not get data.
            report["auth_status"] = client.get("/api/plans").status_code
            marker = "ARENA_STRESS_99999"
            code, body = call(client, "POST", "/api/design", {
                "user_request": (f"离线交接验收 {marker}：先由 diagnosis_agent 提问并等待用户输入，"
                                 f"再用 file_creator 创建 stress_{marker}_artifact.txt。")})
            if code != 200 or not body.get("design"):
                return fail("design", http=code)
            design_id = body["design"]["design_id"]
            code, body = call(client, "POST", f"/api/design/{design_id}/approve", {
                "design_id": design_id, "approved": True})
            if code != 200 or not body.get("plan"):
                return fail("approve", http=code)
            plan_id = body["plan"]["plan_id"]
            report["plan_id"] = plan_id
            code, body = call(client, "GET", f"/api/plan/{plan_id}")
            if code != 200:
                return fail("get_plan", http=code)
            plan = body["plan"]
            steps = [s for seg in plan["segments"] for s in seg["steps"]]
            agents = [s for s in steps if s.get("tool") == "diagnosis_agent"]
            files = [s for s in steps if s.get("tool") == "file_creator"]
            if len(agents) != 1 or len(files) != 1:
                return fail("plan_shape", tools=[s.get("tool") for s in steps])
            agent_id = agents[0]["step_id"]
            file_id = files[0]["step_id"]
            code, body = call(client, "POST", f"/api/plan/{plan_id}/execute")
            if code != 200:
                return fail("execute_start", http=code)
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                code, body = call(client, "GET", f"/api/plan/{plan_id}")
                visible = body.get("plan") or {}
                if code == 200 and visible.get("status") in {"Paused", "Failed", "Completed"}:
                    break
                time.sleep(.15)
            report["first_status"] = visible.get("status")
            report["resume_from_step_id"] = visible.get("resume_from_step_id")
            shown_step = next((s for seg in visible.get("segments", []) for s in seg["steps"]
                               if s["step_id"] == agent_id), {})
            report["needs_input_visible"] = bool((shown_step.get("outputs") or {}).get("needs_input"))
            if (visible.get("status") != "Paused" or visible.get("resume_from_step_id") != agent_id
                    or not report["needs_input_visible"]):
                return fail("did_not_pause_for_input")
            # Invalid target must be rejected and must not mutate the checkpoint.
            wrong, _ = call(client, "POST", f"/api/plan/{plan_id}/steps/{file_id}/input",
                            {"input": {"answer": "WRONG"}})
            report["wrong_target_http"] = wrong
            if wrong != 409:
                return fail("wrong_target_accepted")
            code, body = call(client, "POST", f"/api/plan/{plan_id}/steps/{agent_id}/input",
                              {"input": {"answer": "READY"}})
            if code != 200 or not body.get("success"):
                return fail("input_submit", http=code)
            code, body = call(client, "GET", f"/api/plan/{plan_id}")
            shown_step = next((s for seg in body.get("plan", {}).get("segments", []) for s in seg["steps"]
                               if s["step_id"] == agent_id), {})
            report["input_projected"] = shown_step.get("inputs", {}).get("user_input") == {"answer": "READY"}
            report["status_after_save"] = body.get("plan", {}).get("status")
            if not report["input_projected"] or report["status_after_save"] != "Paused":
                return fail("input_not_visible_or_unauthorized_resume")

            # Burst concurrent resume clicks: one authoritatively starts, others
            # must get 409; no second Run or duplicate Agent work is allowed.
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                answers = list(pool.map(lambda _: call(client, "POST", f"/api/plan/{plan_id}/resume")[0],
                                        range(8)))
            report["resume_http_statuses"] = dict(collections.Counter(answers))
            if answers.count(200) != 1 or any(x not in (200, 409) for x in answers):
                return fail("resume_race_inconsistent")
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                code, body = call(client, "GET", f"/api/plan/{plan_id}")
                visible = body.get("plan") or {}
                if code == 200 and visible.get("status") in {"Completed", "Failed", "Paused"}:
                    break
                time.sleep(.15)
            report["final_status"] = visible.get("status")
            if visible.get("status") != "Completed":
                return fail("resume_did_not_complete")
            report["final_step_statuses"] = [s["status"] for seg in visible["segments"] for s in seg["steps"]]
            if report["final_step_statuses"] != ["Success", "Success"]:
                return fail("incorrect_step_status")
            artifact = root / "workspace" / f"stress_{marker}_artifact.txt"
            report["artifact_ok"] = artifact.exists() and marker in artifact.read_text(encoding="utf-8")
            code, body = call(client, "GET", f"/api/plan/{plan_id}/runs")
            report["run_statuses"] = [r.get("status") for r in body.get("runs", [])]
            events = state.database.get_step_events(plan_id, agent_id)
            report["agent_events"] = [e["event_type"] for e in events]
            report["input_after_complete_http"] = call(
                client, "POST", f"/api/plan/{plan_id}/steps/{agent_id}/input",
                {"input": {"answer": "LATE"}})[0]
            report["success"] = (report["artifact_ok"] and report["run_statuses"].count("Completed") == 1
                                 and report["run_statuses"].count("Paused") == 1
                                 and report["agent_events"].count("needs_input") == 1
                                 and "input_supplied" in report["agent_events"]
                                 and report["input_after_complete_http"] == 409
                                 and report["auth_status"] == 401)
            report["stage"] = "done" if report["success"] else "invariant_mismatch"
            return 0 if report["success"] else 1
    except BaseException as exc:
        report.update(stage="harness_exception", exception_type=type(exc).__name__)
        return 1
    finally:
        report["timings_ms"] = {route: {"count": len(values), "max": max(values)}
                                for route, values in timings.items()}
        calls = root / "model_calls.jsonl"
        report["real_model_requests"] = sum(json.loads(line).get("event") == "start"
                                            for line in calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0
        report["temp_root"] = str(root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k in
                          {"stage", "success", "first_status", "final_status", "resume_http_statuses",
                           "real_model_requests", "run_statuses", "exception_type"}}, ensure_ascii=False))
        print("EVIDENCE", args.output)
        if report["success"]:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
