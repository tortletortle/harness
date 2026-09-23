"""Isolated HTTP full-flow and load probe for the live EAS API.

Starts one loopback-only child server with a private temp DB/workspace; never
uses the host's running service or data/eas.db. Real-model mode uses a journaled
provider-call cap enforced inside the child BEFORE each network request.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
from contextlib import closing
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

if __package__:
    from .stress_budget import BudgetUnavailable, read_budget
else:
    from stress_budget import BudgetUnavailable, read_budget

_REPO = Path(__file__).resolve().parents[1]
_TERMINAL = {"Completed", "Failed", "Paused", "RolledBack"}


def _percentile(values: list[float], frac: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(0, min(len(ordered) - 1, int(len(ordered) * frac + 0.9999) - 1))], 2) if ordered else 0.0


class Meter:
    def __init__(self):
        self.rows: list[tuple[str, int, float]] = []
        self.errors: list[dict] = []

    async def request(self, client, method: str, path: str, *, category: str, body=None):
        start = time.perf_counter()
        try:
            response = await client.request(method, path, json=body)
            try:
                data = response.json()
            except ValueError:
                data = {}
            status = response.status_code
        except Exception as exc:
            data = {}
            status = 0
            if len(self.errors) < 25:
                self.errors.append({"route": category, "status": 0, "error_type": type(exc).__name__})
        elapsed = (time.perf_counter() - start) * 1000
        self.rows.append((category, status, elapsed))
        if status >= 500 and len(self.errors) < 25:
            # Provider/HTTP error strings can contain private upstream content:
            # record only route and status, never the response body.
            self.errors.append({"route": category, "status": status})
        return status, data if isinstance(data, dict) else {}

    def summary(self):
        routes: dict[str, list[tuple[int, float]]] = collections.defaultdict(list)
        for route, status, ms in self.rows:
            routes[route].append((status, ms))
        return {route: {"count": len(rows),
                        "statuses": dict(collections.Counter(status for status, _ in rows)),
                        "p50_ms": _percentile([ms for _, ms in rows], .5),
                        "p95_ms": _percentile([ms for _, ms in rows], .95),
                        "max_ms": round(max(ms for _, ms in rows), 2)}
                for route, rows in sorted(routes.items())}


async def _flow(index: int, client, meter: Meter, root: Path, mode: str, *, timeout_s: float):
    flow_started = time.perf_counter()
    marker = f"ARENA_STRESS_{index:05d}"
    path = f"stress_{marker}_artifact.txt"
    request = (f"隔离测试 {marker}：只使用 file_creator，在当前工作区创建且只创建相对路径 {path}，"
               f"文件内容包含 {marker}。禁止网络搜索、shell、其他工具和修改已有文件。"
               "每个步骤附带确定性 file_exists 验收，设计及计划尽可能简短。")
    item = {"marker": marker, "stage": "design", "success": False}
    code, payload = await meter.request(client, "POST", "/api/design", category="design", body={"user_request": request})
    if code != 200 or not payload.get("design"):
        item["http"] = code
        return item
    design_id = payload["design"]["design_id"]
    item["design_id"] = design_id
    item["stage"] = "approve"
    code, payload = await meter.request(client, "POST", f"/api/design/{design_id}/approve",
                                        category="approve", body={"design_id": design_id, "approved": True})
    if code != 200 or not payload.get("plan"):
        item["http"] = code
        return item
    plan = payload["plan"]
    plan_id = plan["plan_id"]
    item["plan_id"] = plan_id
    code, detail = await meter.request(client, "GET", f"/api/plan/{plan_id}", category="plan_get")
    if code != 200:
        item.update(stage="plan_get", http=code)
        return item
    plan = detail.get("plan") or {}
    steps = [step for segment in plan.get("segments", []) for step in segment.get("steps", [])]
    item["steps"] = len(steps)
    # The model is untrusted: do not execute a plan with unexpected effects.
    safe = bool(steps) and len(steps) <= 8
    for step in steps:
        inputs = step.get("inputs") or {}
        filename = inputs.get("path")
        safe &= (step.get("tool") == "file_creator" and isinstance(filename, str)
                 and Path(filename).name == filename and filename.startswith(f"stress_{marker}_")
                 and isinstance(inputs.get("content"), str) and len(inputs["content"]) <= 4000)
    if not safe:
        item["stage"] = "unsafe_plan_blocked"
        item["tools"] = [step.get("tool") for step in steps]
        return item

    item["stage"] = "execute"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code, payload = await meter.request(client, "POST", f"/api/plan/{plan_id}/execute",
                                            category="execute")
        if code == 200 and payload.get("success"):
            break
        if code != 409:  # 409 is expected while another plan owns the main workspace.
            item["http"] = code
            return item
        await asyncio.sleep(.18 if mode == "offline" else .5)
    else:
        item["stage"] = "execution_queue_timeout"
        return item

    item["stage"] = "poll"
    while time.monotonic() < deadline:
        code, payload = await meter.request(client, "GET", f"/api/plan/{plan_id}", category="plan_poll")
        if code == 0 or code >= 500:
            item.update(stage="api_fault", http=code)
            return item
        status = (payload.get("plan") or {}).get("status") if code == 200 else None
        if status in _TERMINAL:
            item["plan_status"] = status
            plan = payload["plan"]
            break
        await asyncio.sleep(.12 if mode == "offline" else 1)
    else:
        item["stage"] = "execution_timeout"
        return item
    if item["plan_status"] != "Completed":
        item["stage"] = "terminal_non_completed"
        item["step_statuses"] = [s.get("status") for seg in plan.get("segments", []) for s in seg.get("steps", [])]
        return item
    item["step_statuses"] = [s.get("status") for seg in plan.get("segments", []) for s in seg.get("steps", [])]
    if not item["step_statuses"] or set(item["step_statuses"]) != {"Success"}:
        item["stage"] = "steps_not_success"
        return item
    artifact = root / "workspace" / path
    if mode == "offline" and (not artifact.exists() or marker not in artifact.read_text(encoding="utf-8")):
        item["stage"] = "artifact_mismatch"
        return item
    if mode == "real" and not any(marker in f.read_text(encoding="utf-8") for f in (root / "workspace").glob(f"stress_{marker}_*") if f.is_file()):
        item["stage"] = "artifact_mismatch"
        return item

    code, runs = await meter.request(client, "GET", f"/api/plan/{plan_id}/runs", category="runs")
    if code != 200 or not any(r.get("status") == "Completed" for r in runs.get("runs", [])):
        item["stage"] = "run_history_missing"
        return item
    item["runs"] = len(runs.get("runs", []))
    # Delivery reporting is asynchronous. Check it without treating eventual
    # report timing as plan execution failure; record independently.
    report_wait_started = time.perf_counter()
    report_deadline = time.monotonic() + (20 if mode == "offline" else 150)
    item["report_registered"] = False
    while time.monotonic() < report_deadline:
        code, artifacts = await meter.request(client, "GET", f"/api/artifacts?plan_id={plan_id}", category="artifacts")
        if code == 0 or code >= 500:
            item.update(stage="report_api_fault", http=code)
            return item
        if code == 200:
            report = next((a for a in artifacts.get("artifacts", [])
                           if a.get("kind") == "delivery_report"), None)
            if report:
                report_meta = report.get("metadata") or {}
                item["report_narrative_status"] = report_meta.get("narrative_status")
                item["narrative_generation_attempts"] = report_meta.get("narrative_generation_attempts")
                if not report.get("content_hash") or not report.get("artifact_id"):
                    item["stage"] = "report_metadata_invalid"
                    return item
                code, _ = await meter.request(client, "GET",
                                              f"/api/artifacts/{report['artifact_id']}/content",
                                              category="report_content")
                if code != 200:
                    item.update(stage="report_content_unavailable", http=code)
                    return item
                item["report_registered"] = True
                item["report_content_verified"] = True
                break
            if any(job.get("status") == "failed" for job in artifacts.get("report_jobs", [])):
                item["stage"] = "report_job_failed"
                item["report_wait_ms"] = round((time.perf_counter() - report_wait_started) * 1000, 2)
                return item
        await asyncio.sleep(.2 if mode == "offline" else 2)
    item["stage"] = ("done" if item.get("report_narrative_status") == "validated" else
                     "report_facts_only" if item["report_registered"] else "report_missing")
    item["success"] = item["stage"] == "done"
    item["report_wait_ms"] = round((time.perf_counter() - report_wait_started) * 1000, 2)
    item["flow_elapsed_ms"] = round((time.perf_counter() - flow_started) * 1000, 2)
    return item


async def _load(root: Path, base: str, token: str, mode: str, count: int, concurrency: int):
    meter = Meter()
    limits = httpx.Limits(max_connections=max(32, concurrency * 2), max_keepalive_connections=16)
    timeout = httpx.Timeout(130.0 if mode == "real" else 35.0)
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"},
                                 timeout=timeout, trust_env=False, limits=limits) as client:
        flows = []
        # Warm-up is strictly serial, so a failure can't start a flood of paid calls.
        warm = await _flow(1, client, meter, root, mode, timeout_s=420 if mode == "real" else 50)
        flows.append(warm)
        if warm["success"] and count > 1:
            sem = asyncio.Semaphore(concurrency)
            stopped = asyncio.Event()
            async def one(index):
                async with sem:
                    if stopped.is_set():
                        return {"marker": f"ARENA_STRESS_{index:05d}",
                                "stage": "skipped_after_peer_failure", "success": False}
                    try:
                        result = await _flow(index, client, meter, root, mode,
                                             timeout_s=420 if mode == "real" else 80)
                    except Exception as exc:
                        result = {"marker": f"ARENA_STRESS_{index:05d}", "stage": "harness_exception",
                                  "success": False, "error_type": type(exc).__name__}
                    if not result["success"]:
                        stopped.set()
                    return result
            tasks = [asyncio.create_task(one(i)) for i in range(2, count + 1)]
            try:
                for finished in asyncio.as_completed(tasks):
                    if not (await finished)["success"]:
                        break  # Cancel queued and in-flight work on the first fault.
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                flows.extend(result for result in await asyncio.gather(*tasks, return_exceptions=True)
                             if isinstance(result, dict))

        if mode == "offline" and warm.get("plan_id") and all(flow["success"] for flow in flows):
            # Read/auth burst against an actual persisted plan. Do not flood the
            # public tunnel or an existing server; this is local loopback only.
            plan_id = warm["plan_id"]
            sem = asyncio.Semaphore(20)
            burst_stopped = asyncio.Event()
            async def read_one(i):
                async with sem:
                    if burst_stopped.is_set():
                        return 0, {}
                    path = f"/api/plan/{plan_id}" if i % 3 else "/api/plans?limit=20"
                    outcome = await meter.request(client, "GET", path, category="read_burst")
                    if outcome[0] != 200:
                        burst_stopped.set()
                    return outcome
            read_tasks = [asyncio.create_task(read_one(i)) for i in range(300)]
            burst = []
            try:
                for finished in asyncio.as_completed(read_tasks):
                    outcome = await finished
                    burst.append(outcome)
                    if outcome[0] != 200:
                        break
            finally:
                for task in read_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*read_tasks, return_exceptions=True)
            auth_results = []
            async with httpx.AsyncClient(base_url=base, timeout=10.0, trust_env=False) as anonymous:
                for _ in range(15):
                    auth_results.append((await anonymous.get(f"/api/plan/{plan_id}")).status_code)
                    if auth_results[-1] != 401:
                        break
            auth_ok = set(auth_results) == {401}
            burst_ok = all(status == 200 for status, _ in burst)
        else:
            auth_ok = None
            burst_ok = None
    return {"flows": flows, "metrics": meter.summary(), "http_errors": meter.errors,
            "auth_denials_ok": auth_ok, "read_burst_ok": burst_ok}


def _get_db_invariants(root: Path):
    db_path = root / "data" / "eas.db"
    if not db_path.exists():
        return {"db_missing": True}
    # sqlite3's context manager commits but does NOT close the connection.
    # On Windows an unclosed handle prevents removing this isolated DB.
    with closing(sqlite3.connect(str(db_path), timeout=10)) as db:
        return {
            "plans_by_status": dict(db.execute("SELECT status, count(*) FROM plans GROUP BY status")),
            "runs_by_status": dict(db.execute("SELECT status, count(*) FROM execution_runs GROUP BY status")),
            "attempts_by_status": dict(db.execute("SELECT status, count(*) FROM execution_attempts GROUP BY status")),
        }


def _usage_summary(root: Path):
    path = root / "model_calls.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    starts = [e for e in events if e.get("event") == "start"]
    finishes = [e for e in events if e.get("event") == "finish"]
    return {"requests_started": len(starts), "requests_finished": len(finishes),
            "ok": sum(e.get("ok") is True for e in finishes),
            "errors": sum(e.get("ok") is False for e in finishes),
            "output_tokens_recorded": sum((e.get("usage") or {}).get("completion_tokens", 0) or 0 for e in finishes)}


def _open_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "real"], required=True)
    parser.add_argument("--flows", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--model-limit", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.flows <= 60 or not 1 <= args.concurrency <= 20:
        parser.error("flows must be 1-60 and concurrency 1-20")
    if (args.mode == "offline" and args.model_limit != 0) or (args.mode == "real" and not 1 <= args.model_limit <= 95):
        parser.error("offline needs zero model calls, real needs 1-95 (reserve prior-call safety margin)")
    budget_path = _REPO / "reports" / "stress-20260923" / "model-budget.sqlite"
    if args.mode == "real":
        try:
            budget = read_budget(budget_path)
        except BudgetUnavailable:
            parser.error("real mode requires an intact, preinitialized cumulative budget ledger")
        if budget["used"] + args.model_limit > budget["cap"]:
            parser.error("per-run model limit exceeds remaining cumulative request budget")
    root = Path(tempfile.mkdtemp(prefix="arena-eas-stress-"))
    token = "arena-stress-" + secrets.token_urlsafe(22)
    port = _open_port()
    env = dict(os.environ)
    env.update({"ARENA_STRESS_ROOT": str(root), "ARENA_STRESS_MODE": args.mode,
                "ARENA_STRESS_MODEL_LIMIT": str(args.model_limit), "EAS_API_TOKEN": token,
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join([str(_REPO), str(_REPO / "python"), env.get("PYTHONPATH", "")])})
    log = (root / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "scripts.stress_guarded_app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    report = {"schema": "isolated-fullflow-stress/v1", "mode": args.mode,
              "flows_requested": args.flows, "concurrency": args.concurrency,
              "model_request_cap": args.model_limit, "isolated_db": True,
              "external_mcp_disabled": True, "notifications_disabled": True}
    start = time.monotonic()
    try:
        base = f"http://127.0.0.1:{port}"
        ready = False
        for _ in range(80):
            if proc.poll() is not None:
                break
            try:
                response = httpx.get(base + "/api/status", headers={"Authorization": f"Bearer {token}"},
                                     timeout=1, trust_env=False)
                ready = response.status_code == 200
                if ready:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.5)
        if not ready:
            report["startup_error"] = "local service did not become healthy"
        else:
            report.update(asyncio.run(_load(root, base, token, args.mode, args.flows, args.concurrency)))
    except BaseException as exc:
        report["harness_error_type"] = type(exc).__name__
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log.close()
        report["duration_s"] = round(time.monotonic() - start, 2)
        report["child_exit_code"] = proc.returncode
        report["model_usage"] = _usage_summary(root)
        if args.mode == "real":
            try:
                report["cumulative_budget"] = read_budget(budget_path)
            except BudgetUnavailable:
                report["cumulative_budget"] = {"error": "unavailable"}
        report["db_invariants"] = _get_db_invariants(root)
        report["temp_root"] = str(root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k in
                          {"mode", "flows_requested", "duration_s", "model_usage", "cumulative_budget", "db_invariants", "startup_error", "harness_error_type", "read_burst_ok", "auth_denials_ok"}},
                         ensure_ascii=False))
        if report.get("flows"):
            print("STAGES", dict(collections.Counter(f["stage"] for f in report["flows"])))
        print("EVIDENCE", args.output)
        # Preserve temp evidence only on failure for diagnosis. Token is only in
        # the child environment, not written to disk or to the evidence JSON.
        successful = ("startup_error" not in report and "harness_error_type" not in report
                      and all(f["success"] for f in report.get("flows", []))
                      and report.get("auth_denials_ok") is not False and report.get("read_burst_ok") is not False
                      and report["model_usage"]["requests_started"] <= args.model_limit
                      and (args.mode == "offline" or
                           report.get("cumulative_budget", {}).get("used", 101) <= 100))
        if successful:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
