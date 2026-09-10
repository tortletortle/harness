"""Strict A-standard real-LLM acceptance.

Runs five independent daily-development requests through design -> approval ->
plan execution without interactive human intervention. Credentials are read by
the server from environment variables; this script never accepts or prints a key.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

TASKS = [
    "在工作区创建一个 Markdown 文件，内容为 Harness real task one，并用 file_creator 完成。",
    "在工作区创建一个 Markdown 文件，内容为 Harness real task two，并用 file_creator 完成。",
    "在工作区创建一个 Markdown 文件，内容为 Harness real task three，并用 file_creator 完成。",
    "在工作区创建一个 Markdown 文件，内容为 Harness real task four，并用 file_creator 完成。",
    "在工作区创建一个 Markdown 文件，内容为 Harness real task five，并用 file_creator 完成。",
]

_ALLOWED_SCHEMES = {"http", "https"}


def _validate_base(base: str, allow_local: bool = False) -> str:
    parsed = urlparse(base)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError(f"仅允许 http/https 且包含 host: {base!r}")
    if allow_local and parsed.hostname in {"127.0.0.1", "::1"}:
        return base
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError(f"拒绝 localhost 目标: {parsed.hostname}")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError(f"host 解析失败: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"目标解析到受限地址: {address}")
    return base


def request(base: str, token: str, path: str, method: str = "GET", body=None, allow_local: bool = False, timeout: int = 300):
    validated = _validate_base(base, allow_local=allow_local)
    url = f"{validated.rstrip('/')}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        payload = json.dumps(body, ensure_ascii=False).encode()
    else:
        payload = None
    try:
        # url derives from validated base; only http/https reach this point.
        with urllib.request.urlopen(req, data=payload, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:500]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("EAS_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--output", type=Path, default=Path("out/real-llm-acceptance-a.json"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--request-timeout", type=int, default=300, help="单次 HTTP 请求超时（秒），覆盖真实 LLM 生成计划耗时")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--allow-local-api", action="store_true", help="仅允许验收脚本访问本机控制面")
    args = parser.parse_args()

    if args.env_file and args.env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(args.env_file, override=False)
        except Exception:
            pass

    token = os.getenv("EAS_API_TOKEN", "")
    if not token:
        token_path = Path("config/api_token")
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    if not token:
        print("FAIL: EAS_API_TOKEN or config/api_token is required")
        return 2
    if not os.getenv("EAS_LLM_API_KEY_STEPFUN") and not os.getenv("EAS_LLM_API_KEY_NEWAPI"):
        print("FAIL: inject a real LLM key via EAS_LLM_API_KEY_STEPFUN or EAS_LLM_API_KEY_NEWAPI")
        return 2

    req_timeout = args.request_timeout
    evidence = {"standard": "A", "task_count": len(TASKS), "tasks": [], "passed": False}
    for index, user_request in enumerate(TASKS, 1):
        item = {"index": index, "request": user_request}
        code, design_response = request(args.base, token, "/api/design", "POST", {"user_request": user_request}, allow_local=args.allow_local_api, timeout=req_timeout)
        if code != 200 or not design_response.get("design"):
            item.update({"passed": False, "stage": "design", "status_code": code, "response": design_response})
            evidence["tasks"].append(item)
            break
        design_id = design_response["design"]["design_id"]
        item["design_id"] = design_id
        code, approval_response = request(args.base, token, f"/api/design/{design_id}/approve", "POST", {"design_id": design_id, "approved": True}, allow_local=args.allow_local_api, timeout=req_timeout)
        if code != 200 or not approval_response.get("plan"):
            item.update({"passed": False, "stage": "approval_plan", "status_code": code, "response": approval_response})
            evidence["tasks"].append(item)
            break
        plan_id = approval_response["plan"]["plan_id"]
        item["plan_id"] = plan_id
        code, execute_response = request(args.base, token, f"/api/plan/{plan_id}/execute", "POST", None, allow_local=args.allow_local_api, timeout=req_timeout)
        if code != 200 or not execute_response.get("success"):
            item.update({"passed": False, "stage": "execute_start", "status_code": code, "response": execute_response})
            evidence["tasks"].append(item)
            break
        deadline = time.time() + args.timeout
        plan = {}
        while time.time() < deadline:
            code, plan_response = request(args.base, token, f"/api/plan/{plan_id}", allow_local=args.allow_local_api, timeout=req_timeout)
            plan = plan_response.get("plan", {}) if code == 200 else {}
            if plan.get("status") in {"Completed", "Failed", "RolledBack"}:
                break
            time.sleep(2)
        code, runs_response = request(args.base, token, f"/api/plan/{plan_id}/runs", allow_local=args.allow_local_api, timeout=req_timeout)
        steps = plan.get("steps", [])
        item.update({
            "plan_status": plan.get("status"),
            "step_statuses": [step.get("status") for step in steps],
            "run_statuses": [run.get("status") for run in runs_response.get("runs", [])],
            "passed": plan.get("status") == "Completed" and bool(steps) and all(step.get("status") == "Success" for step in steps),
        })
        evidence["tasks"].append(item)
        if not item["passed"]:
            break

    evidence["passed"] = len(evidence["tasks"]) == 5 and all(item.get("passed") for item in evidence["tasks"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
