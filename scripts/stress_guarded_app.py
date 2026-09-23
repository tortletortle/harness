"""ASGI wrapper for isolated EAS stress tests, never a production entry point.

Every OpenAI-compatible provider call is journaled *before* it leaves this
process.  The journal is append-only and contains no prompts or credentials.
Offline mode replaces the shared LLM interface while keeping a zero-call
provider guard, so unexpected direct provider calls fail closed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from scripts.stress_budget import BudgetUnavailable, claim_budget

_ROOT = Path(os.environ["ARENA_STRESS_ROOT"]).resolve()
if not _ROOT.name.startswith("arena-eas-stress-") or not _ROOT.is_relative_to(Path(tempfile.gettempdir()).resolve()):
    raise RuntimeError("stress root must be a dedicated directory under the OS temp directory")
_MODE = os.environ["ARENA_STRESS_MODE"]
if _MODE not in {"offline", "real"}:
    raise RuntimeError("unrecognized stress mode")
_TOKEN = os.environ.get("EAS_API_TOKEN", "")
if not _TOKEN.startswith("arena-stress-"):
    raise RuntimeError("isolated API token is required")
_LIMIT = int(os.environ["ARENA_STRESS_MODEL_LIMIT"])
if _LIMIT < 0 or (_MODE == "offline" and _LIMIT != 0) or _LIMIT > 100:
    raise RuntimeError("invalid model request limit")

_REPO = Path(__file__).resolve().parents[1]
_BUDGET_DB = _REPO / "reports" / "stress-20260923" / "model-budget.sqlite"
sys.path.insert(0, str(_REPO / "python"))
(_ROOT / "workspace").mkdir(parents=True, exist_ok=True)
(_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_ROOT / "logs").mkdir(parents=True, exist_ok=True)
os.environ["EAS_DB_PATH"] = str(_ROOT / "data" / "eas.db")
os.environ["EAS_WORKSPACE_ROOT"] = str(_ROOT / "workspace")
os.environ["EAS_LOG_DIR"] = str(_ROOT / "logs")
os.environ["EAS_LLM_MAX_CONCURRENCY"] = "2"
os.chdir(_ROOT)

from llm_client import LLMRequestError, LLMResponse, OpenAICompatibleProvider  # noqa: E402

_JOURNAL = _ROOT / "model_calls.jsonl"
_LOCK = threading.Lock()
with _LOCK:
    _used = sum(json.loads(line).get("event") == "start" for line in _JOURNAL.read_text(encoding="utf-8").splitlines()) if _JOURNAL.exists() else 0
if _used > _LIMIT:
    raise RuntimeError("existing journal already exceeds model cap")
_original_chat = OpenAICompatibleProvider.chat


def _append(entry: dict) -> None:
    # Do not put prompts, API keys, base URLs or responses in this file.
    with _JOURNAL.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


async def _limited_chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
    global _used
    with _LOCK:
        if _used >= _LIMIT:
            raise LLMRequestError("isolated stress model cap reached", status_code=429, retryable=False)
        cumulative_seq = None
        if _MODE == "real":
            try:
                cumulative_seq = claim_budget(_BUDGET_DB)
            except BudgetUnavailable as exc:
                raise LLMRequestError("isolated stress cumulative model cap unavailable or reached",
                                      status_code=429, retryable=False) from exc
        next_seq = _used + 1
        # Limit each answer's billable size independently of the 100-request cap.
        token_limit = max(1, min(int(max_tokens or 4000), 4096))
        _append({"event": "start", "seq": next_seq, "cumulative_seq": cumulative_seq,
                 "provider": self.name,
                 "model": str(model), "max_tokens": token_limit, "time": time.time()})
        _used = next_seq
    started = time.monotonic()
    try:
        response = await _original_chat(self, messages, model, temperature=temperature,
                                        max_tokens=token_limit, **kwargs)
        with _LOCK:
            _append({"event": "finish", "seq": next_seq, "ok": True,
                     "elapsed_s": round(time.monotonic() - started, 3),
                     "usage": getattr(response, "usage", None)})
        return response
    except BaseException as exc:
        with _LOCK:
            _append({"event": "finish", "seq": next_seq, "ok": False,
                     "elapsed_s": round(time.monotonic() - started, 3),
                     "error_type": type(exc).__name__,
                     "status": getattr(exc, "status_code", None)})
        raise


OpenAICompatibleProvider.chat = _limited_chat

# Import server only after the DB, workspace and provider guard have been set.
from api import server as _server  # noqa: E402

_original_load = _server._load_models_config


def _isolated_config(*args, **kwargs):
    config = _original_load(*args, **kwargs)
    # These outbound integrations are unrelated to the model experiment.
    config["mcp_servers"] = {}
    config["notify"] = {"enabled": False}
    config["context_compression"] = {"enabled": False}
    config["reports"] = {"timeout_seconds": 30 if _MODE == "offline" else 120,
                         "shutdown_grace_seconds": 10}
    return config


_server._load_models_config = _isolated_config
_original_lifespan = _server.app.router.lifespan_context


def _offline_response(messages) -> str:
    system = "\n".join(getattr(m, "content", "") or "" for m in messages if m.role == "system")
    user = "\n".join(getattr(m, "content", "") or "" for m in messages if m.role == "user")
    if "只完成当前 WorkItem" in user:
        waiting = "user_input" not in user
        return json.dumps({
            "status": "needs_input" if waiting else "completed",
            "output": {"question": "请提供确认词 READY", "questions": ["请提供确认词 READY"]}
                      if waiting else {"answer": "READY"},
            "artifacts": [], "facts": [], "error": None,
        }, ensure_ascii=False)
    if "系统架构师" in system:
        tools = ["file_creator", "diagnosis_agent"] if "ARENA_STRESS_99999" in user else ["file_creator"]
        return json.dumps({
            "architecture": {"components": tools, "interactions": ["local file"]},
            "difficulty": {"overall": "easy", "breakdown": {}},
            "required_tools": [{"tool_id": name, "availability": "available"} for name in tools],
            "overall_tool_completeness": 1.0, "risks": [],
        }, ensure_ascii=False)
    if "执行计划专家" in system:
        match = re.search(r"ARENA_STRESS_[0-9]{5}", user)
        if not match:
            raise RuntimeError("offline planner did not receive unique stress identifier")
        marker = match.group()
        first = f"stress_{marker}_one.txt"
        final = f"stress_{marker}_artifact.txt"
        if marker == "ARENA_STRESS_99999":
            return json.dumps({
                "segments": [{"description": "agent handoff", "steps": [
                    {"step_id": "ask", "description": "request user input", "tool": "diagnosis_agent",
                     "inputs": {"step_description": "request input", "failure_reason": "waiting",
                                "tool": "file_creator"}, "check_criteria": "手动检查"},
                    {"step_id": "write", "depends_on": ["ask"], "description": "write after reply",
                     "tool": "file_creator", "inputs": {"path": final, "content": "complete " + marker},
                     "acceptance": [{"type": "file_exists", "path": final, "kind": "file"}]},
                ]}], "implementation_list": [final],
            }, ensure_ascii=False)
        return json.dumps({
            "segments": [{"description": "isolated two-step workflow", "steps": [
                {"step_id": "first", "description": "create marker", "tool": "file_creator",
                 "inputs": {"path": first, "content": marker},
                 "acceptance": [{"type": "file_exists", "path": first, "kind": "file"}]},
                {"step_id": "second", "depends_on": ["first"], "description": "create artifact",
                 "tool": "file_creator", "inputs": {"path": final, "content": "complete " + marker},
                 "acceptance": [{"type": "file_exists", "path": final, "kind": "file"},
                                {"type": "content_contains", "path": final, "text": marker}]},
            ]}], "implementation_list": [first, final],
        }, ensure_ascii=False)
    if "结果验证专家" in system:
        return "YES"
    if "执行报告撰写者" in system:
        return "测试执行结果仅供隔离环境验证；历史运行与人工操作情况以附带事实表为准。"
    raise RuntimeError("unexpected model usage in offline stress workflow")


@asynccontextmanager
async def _stress_lifespan(app):
    async with _original_lifespan(app):
        # The service's DB and both persistent caches must share this private
        # root. Merely setting EAS_DB_PATH was not sufficient before the cache fix.
        stored = [_server.state.database.db_path,
                  _server.state.designer.design_cache.cache_file,
                  _server.state.designer.plan_cache.cache_file]
        if any(not Path(path).resolve().is_relative_to(_ROOT) for path in stored):
            raise RuntimeError("stress service attempted shared DB or cache storage")
        if _MODE == "real" and any(type(provider) is not OpenAICompatibleProvider
                                   for provider in _server.state.llm_client.providers.values()):
            raise RuntimeError("stress service registered a model provider without an outbound guard")
        if _server.state.mcp_clients or (_server.state.notifier and _server.state.notifier.enabled):
            raise RuntimeError("external MCP or notification is enabled in isolated stress")
        if _MODE == "offline":
            async def fake_chat(provider_name, messages, model, **kwargs):
                await asyncio.sleep(0.01)
                return LLMResponse(content=_offline_response(messages), model=model,
                                   finish_reason="stop", usage={"prompt_tokens": 0, "completion_tokens": 0})
            _server.state.llm_client.chat = fake_chat
        # Reject searches rather than letting a model invoke an external URL.
        async def blocked_search(*args, **kwargs):
            return {"success": False, "error": "external search disabled in isolated stress", "results": []}
        _server.state.dispatcher.request_search = blocked_search
        yield


_server.app.router.lifespan_context = _stress_lifespan
app = _server.app
