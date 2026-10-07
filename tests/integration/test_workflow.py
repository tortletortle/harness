# -*- coding: utf-8 -*-
"""离线集成测试：命中当前 python/api/server.py 真实路由的完整工作流。

进程内 `with TestClient(app)` 触发真实 lifespan（provider 注册、工具 registry、
executor、授权、数据库装配全部真实），但用一个「按序返回设计书/执行计划 JSON」
的确定性 provider 完全离线跑 设计 → 批准 → 生成计划 → 执行 全链路，真实工具
file_creator / test_runner 落盘执行。

- conftest 的隔离工作区根（EAS_WORKSPACE_ROOT=临时目录）+ 本文件 monkeypatch 的
  EAS_DB_PATH/EAS_LOG_DIR 保证绝不写仓库根与真实 data/eas.db。
- monkeypatch api.server._load_models_config 返回受控 config：空 api_keys（不注册
  真实 provider），designer/executor 主模型指向 deterministic —— 既不触发离线护栏
  的 secret_store 拒绝，也不连任何外部上游。
"""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from llm_client import LLMProvider, LLMResponse
from tools.workspace import resolve_workspace_root

_TOKEN = "offline-integration-token"


class _ScriptedProvider(LLMProvider):
    """按调用顺序返回固定 JSON 的确定性 provider（离线）。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self._last = json.dumps({"ok": True})
        self.calls = []

    @property
    def name(self):
        return "deterministic"

    async def chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
        self.calls.append(messages)
        if self._responses:
            self._last = self._responses.pop(0)
        return LLMResponse(content=self._last, model=model, finish_reason="stop")

    async def health_check(self):
        return True


DESIGN_JSON = json.dumps({
    "architecture": {"components": ["后端"], "interactions": []},
    "difficulty": {"overall": "medium", "breakdown": {}},
    "required_tools": [{"tool_id": "file_creator", "availability": "available"}],
    "overall_tool_completeness": 1.0,
    "risks": [],
}, ensure_ascii=False)


def _plan_json(tool, inputs, acceptance, description):
    return json.dumps({
        "segments": [{"description": description, "steps": [
            {"description": description, "tool": tool, "inputs": inputs,
             "acceptance": acceptance},
        ]}],
        "implementation_list": [],
    }, ensure_ascii=False)


def _fake_config(root=None):
    primary = {"provider": "deterministic", "model": "test-model",
               "temperature": 0.1, "max_tokens": 2000}
    return {
        "api_keys": {},
        "base_urls": {},
        "models": {
            "designer": {"primary": dict(primary)},
            "executor": {"primary": dict(primary), "max_concurrency": 1},
        },
        "parallel_graphs": False,
        "authorization": {},
        "notify": {},
        "budget": {},
        "retry": {},
        "skills": {},
        "chat": {},
    }


@pytest.fixture
def live_client(monkeypatch, tmp_path, request):
    import api.server as srv
    from api.auth import reset_token_cache

    monkeypatch.setenv("EAS_API_TOKEN", _TOKEN)
    monkeypatch.setenv("EAS_DB_PATH", str(tmp_path / "eas.db"))
    monkeypatch.setenv("EAS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(srv, "_load_models_config", _fake_config)
    reset_token_cache()

    plan = request.param
    provider = _ScriptedProvider([DESIGN_JSON, plan])
    with TestClient(srv.app) as client:
        srv.state.llm_client.register_provider(provider)
        yield client, provider


def _wait_terminal(client, plan_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/plan/{plan_id}", headers={"Authorization": f"Bearer {_TOKEN}"})
        data = r.json()
        status = data["plan"]["status"]
        if status in {"Completed", "Failed", "RolledBack"}:
            return status, data
        time.sleep(0.2)
    raise AssertionError(f"plan {plan_id} 未在 {timeout}s 内到达终态")


def _run_flow(client, user_request):
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    r = client.post("/api/design", json={"user_request": user_request}, headers=headers)
    assert r.status_code == 200, r.text
    design_id = r.json()["design"]["design_id"]

    r = client.post(f"/api/design/{design_id}/approve",
                    json={"design_id": design_id, "approved": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json().get("approved") or (r.json().get("design") or {}).get("user_approval")
    r = client.post(f"/api/design/{design_id}/generate-plan", headers=headers)
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan"]["plan_id"]

    r = client.post(f"/api/plan/{plan_id}/execute", headers=headers)
    assert r.status_code == 200, r.text
    return plan_id


@pytest.mark.parametrize(
    "live_client",
    [_plan_json("file_creator",
                {"path": "a.txt", "content": "hello integration"},
                [{"type": "file_exists", "path": "a.txt", "kind": "file"}],
                "创建 a.txt")],
    indirect=True,
)
def test_full_route_success_completes_and_writes_file(live_client):
    client, provider = live_client
    plan_id = _run_flow(client, "创建 a.txt 内容 hello integration")

    status, data = _wait_terminal(client, plan_id)
    assert status == "Completed", f"期望 Completed，实际 {status}: {data}"
    assert (resolve_workspace_root() / "a.txt").exists(), "file_creator 产物未落盘"
    assert len(provider.calls) >= 2, "设计书+执行计划应各消费一次确定性调用"


@pytest.mark.parametrize(
    "live_client",
    [_plan_json("test_runner",
                {"suite": "pytest", "test_path": "output/does-not-exist_test.py"},
                [{"type": "exit_code", "equals": 0}],
                "运行不存在的测试文件")],
    indirect=True,
)
def test_full_route_failing_step_ends_failed(live_client):
    client, _provider = live_client
    plan_id = _run_flow(client, "运行一个必然失败的测试")

    status, data = _wait_terminal(client, plan_id)
    assert status == "Failed", f"期望 Failed，实际 {status}: {data}"
