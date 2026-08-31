# -*- coding: utf-8 -*-
"""coding_agent patch 模式测试（削接缝动作 A）：search/replace 补丁契约。

覆盖：
1. patch 模式单轮修改现有文件（SEARCH 唯一命中）。
2. patch 模式新建文件（空 SEARCH 段）。
3. 补丁错误（not_found）→ 反馈回流 → 第 2 轮收敛；消息前缀复用。
4. 最后一轮仍失败 → fail-closed，文件保持原状。
5. 新建冲突（文件已存在 + 空 SEARCH）→ fail-closed。
6. 环境变量 EAS_CODING_AGENT_OUTPUT_MODE=patch 开启模式（inputs 不带 output_mode）。
"""

import pytest

from authorization import AuthorizationManager, GrantLimits
from capabilities import CapabilityRegistry
from coding_agent import CodingAgentTool
from executor import DefaultExecutor
from llm_client import LLMClient, LLMProvider, LLMResponse
from models import Step
from tools import get_registry

ORIGINAL = "def greet():\n    return 1\n"
EDITED = "def greet():\n    return 'hello, world'\n"
TARGET = "src/app.py"


def _patch_block(search: str, replace: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def _make_step(**extra) -> Step:
    inputs = {
        "path": TARGET,
        "instruction": "把返回值改成 'hello, world'",
        "allowed_tools": ["text_editor"],
        "output_mode": "patch",
    }
    inputs.update(extra)
    if inputs.get("output_mode") is None:
        inputs.pop("output_mode", None)
    return Step(
        order=1,
        description="修改 greet 函数返回值",
        tool="coding_agent",
        inputs=inputs,
        check_criteria="",
        acceptance=[{"type": "content_contains", "path": TARGET, "text": "hello, world"}],
    )


class _SeqProvider(LLMProvider):
    """按调用次序返回不同响应的多轮测试桩（与 test_coding_agent.py 同构）。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    @property
    def name(self):
        return "sequence"

    async def chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
        self.calls.append(list(messages))  # 快照：messages 会被后续轮原地追加
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return LLMResponse(content=self.responses[idx], model=model, finish_reason="stop")

    async def health_check(self):
        return True


def _make_env(monkeypatch, tmp_path, responses):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    manager = AuthorizationManager(workspace_root=tmp_path)
    llm = LLMClient()
    provider = _SeqProvider(responses)
    llm.register_provider(provider)
    agent = CodingAgentTool(
        llm_client=llm,
        authorization_manager=manager,
        tool_registry=get_registry(),
        provider_name="sequence",
        model="test-model",
    )
    registry = CapabilityRegistry()
    registry.register(agent)
    executor = DefaultExecutor(
        llm_client=llm,
        model_config={"primary": {"provider": "sequence", "model": "test-model"}},
        tool_registry=get_registry(),
        database=None,
        authorization_manager=manager,
        capability_registry=registry,
    )
    return manager, executor, provider


def _seed(tmp_path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text(ORIGINAL, encoding="utf-8")


@pytest.mark.asyncio
async def test_patch_mode_edits_file_single_turn(monkeypatch, tmp_path):
    _seed(tmp_path)
    manager, executor, provider = _make_env(
        monkeypatch, tmp_path, [_patch_block("    return 1", "    return 'hello, world'")]
    )
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-patch",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-patch")

    assert result["success"], result
    final = (tmp_path / "src" / "app.py").read_text(encoding="utf-8")
    assert "hello, world" in final and "return 1" not in final
    facts = {f["key"]: f["value"] for f in result["output"]["agent_facts"]}
    assert facts["coding_agent_output_mode"] == "patch"
    assert facts["coding_agent_turn1_blocks_applied"] == 1
    assert grant.used_model_calls == 1
    # patch 提示词走补丁块契约，不再要求 JSON 全文
    assert "search/replace 补丁块" in provider.calls[0][-1].content
    assert "只输出 JSON" not in provider.calls[0][-1].content


@pytest.mark.asyncio
async def test_patch_mode_creates_new_file(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    manager, executor, provider = _make_env(monkeypatch, tmp_path, [_patch_block("", EDITED)])
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-patch-new",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-patch-new")

    assert result["success"], result
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == EDITED


@pytest.mark.asyncio
async def test_patch_mode_error_then_retry_converges(monkeypatch, tmp_path):
    _seed(tmp_path)
    bad = _patch_block("    return 999", "    return 'hello, world'")  # 文件中不存在
    good = _patch_block("    return 1", "    return 'hello, world'")
    manager, executor, provider = _make_env(monkeypatch, tmp_path, [bad, good])
    step = _make_step(max_turns=2)
    grant = manager.issue_for_step(
        "plan-patch-retry",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=6, max_model_calls=2),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-patch-retry")

    assert result["success"], result
    assert "hello, world" in (tmp_path / "src" / "app.py").read_text(encoding="utf-8")
    facts = {f["key"]: f["value"] for f in result["output"]["agent_facts"]}
    assert facts["coding_agent_turn1_patch_error"] == "not_found"
    assert facts["coding_agent_turns_used"] == 2
    assert grant.used_model_calls == 2
    # 第二轮提示词带补丁错误反馈；消息序列前缀复用（assistant + user 各追加一条）
    first_msgs, second_msgs = provider.calls[0], provider.calls[1]
    assert len(second_msgs) == len(first_msgs) + 2
    for i, m in enumerate(first_msgs):
        assert second_msgs[i].role == m.role
        assert second_msgs[i].content == m.content
    assert second_msgs[len(first_msgs)].role == "assistant"
    assert second_msgs[-1].role == "user"
    assert "SEARCH 内容在文件中不存在" in second_msgs[-1].content


@pytest.mark.asyncio
async def test_patch_mode_fails_closed_on_last_turn_error(monkeypatch, tmp_path):
    _seed(tmp_path)
    manager, executor, provider = _make_env(
        monkeypatch, tmp_path, [_patch_block("    return 999", "x")]
    )
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-patch-fail",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-patch-fail")

    assert result["success"] is False
    # 文件保持原状（fail-closed，不伪装成功）
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_patch_mode_new_file_conflict_fails_closed(monkeypatch, tmp_path):
    _seed(tmp_path)
    manager, executor, provider = _make_env(monkeypatch, tmp_path, [_patch_block("", EDITED)])
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-patch-conflict",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-patch-conflict")

    assert result["success"] is False
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_env_var_enables_patch_mode(monkeypatch, tmp_path):
    _seed(tmp_path)
    monkeypatch.setenv("EAS_CODING_AGENT_OUTPUT_MODE", "patch")
    manager, executor, provider = _make_env(
        monkeypatch, tmp_path, [_patch_block("    return 1", "    return 'hello, world'")]
    )
    step = _make_step(output_mode=None)  # inputs 不带 output_mode → 环境变量生效
    grant = manager.issue_for_step(
        "plan-patch-env",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-patch-env")

    assert result["success"], result
    facts = {f["key"]: f["value"] for f in result["output"]["agent_facts"]}
    assert facts["coding_agent_output_mode"] == "patch"
    assert "hello, world" in (tmp_path / "src" / "app.py").read_text(encoding="utf-8")
