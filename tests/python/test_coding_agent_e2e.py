"""coding_agent 真实 LLM 端到端：Grant → 单轮真实模型 → 受权写回 → 确定性验收。

这是设计稿 §19"Agent 即工具第二阶段"最小闭环的真实验证：
不走 mock，用 config/models.yaml 里注册的真实 provider 完成一次受权文件修改。

运行方式（会消耗真实 API 额度，默认跳过）：

    RUN_REAL_LLM_EVAL=1 python -m pytest tests/python/test_coding_agent_e2e.py -q --no-cov
"""

import os
from pathlib import Path

import pytest
import yaml

RUN_REAL = os.environ.get("RUN_REAL_LLM_EVAL") == "1"
pytestmark = pytest.mark.skipif(not RUN_REAL, reason="需要 RUN_REAL_LLM_EVAL=1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from authorization import AuthorizationManager, GrantLimits
from capabilities import CapabilityRegistry
from coding_agent import CodingAgentTool
from executor import DefaultExecutor
from llm_client import LLMClient, OpenAICompatibleProvider
from models import Step
from tools import get_registry

ORIGINAL = "def greet():\n    return 1\n"


@pytest.mark.asyncio
async def test_coding_agent_real_llm_edits_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))

    config = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    llm = LLMClient()
    registered = 0
    for provider_name, api_key in (config.get("api_keys") or {}).items():
        base_url = (config.get("base_urls") or {}).get(provider_name)
        if not api_key or not base_url:
            continue
        llm.register_provider(
            OpenAICompatibleProvider(name=provider_name, base_url=base_url, api_key=api_key)
        )
        registered += 1
    assert registered, "config/models.yaml 缺少可用 provider"

    model_cfg = config.get("models", {}).get("executor", {})
    assert model_cfg.get("primary", {}).get("provider"), "executor 模型配置缺失"

    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "greet.py").write_text(ORIGINAL, encoding="utf-8")

    manager = AuthorizationManager(workspace_root=tmp_path)
    agent = CodingAgentTool(
        llm_client=llm,
        authorization_manager=manager,
        tool_registry=get_registry(),
        provider_name=model_cfg["primary"]["provider"],
        model=model_cfg["primary"]["model"],
    )
    registry = CapabilityRegistry()
    registry.register(agent)
    executor = DefaultExecutor(
        llm_client=llm,
        model_config=model_cfg,
        tool_registry=get_registry(),
        authorization_manager=manager,
        capability_registry=registry,
    )

    step = Step(
        order=1,
        description="把 greet 函数的返回值从 1 改为字符串 'hello, world'，函数其余部分保持不变",
        tool="coding_agent",
        inputs={
            "path": "src/greet.py",
            "instruction": "把 return 1 改成 return 'hello, world'，只做这一处修改",
            "allowed_tools": ["text_editor"],
        },
        check_criteria="",
        acceptance=[
            {"type": "content_contains", "path": "src/greet.py", "text": "hello, world"},
            {"type": "content_contains", "path": "src/greet.py", "text": "def greet"},
        ],
    )
    grant = manager.issue_for_step(
        "plan-agent-e2e",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-agent-e2e")

    assert result["success"], f"coding_agent 真实 E2E 失败: {result}"
    assert result.get("check_passed") is True
    assert result.get("acceptance_status") == "passed"
    final_text = (src / "greet.py").read_text(encoding="utf-8")
    assert "hello, world" in final_text
    assert "def greet" in final_text
    # 单轮约束在真实模型路径上同样成立。
    assert grant.used_model_calls == 1
