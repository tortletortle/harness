"""coding_agent 全流程设计（1.4）的结构测试（确定性，不调真实 LLM）。

覆盖四层：
A. 工具目录聚合 —— Dispatcher 把 agent_tool 能力合并进工具目录，Designer 可见。
B. 计划契约校验 —— validate_agent_steps 对 coding_agent 步骤 fail-closed。
C. 计划提示词 —— 白名单放行 coding_agent 并携带完整契约。
D. 设计期打回 —— 含非法 agent 步骤的 LLM 计划被拒绝重试，绝不带病入库。
"""

import json

import pytest

from capabilities import CapabilityRegistry
from coding_agent import CodingAgentTool
from designer import DefaultDesigner, GenerationError
from dispatcher import Dispatcher
from llm_client import DeterministicProvider, LLMClient
from models import DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment, ToolRequirement
from tools import get_registry
from utils.json_fixer import validate_agent_steps


# ===== A. 工具目录聚合 =====


@pytest.mark.asyncio
async def test_dispatcher_merges_agent_tools_into_catalog():
    agent = CodingAgentTool(
        llm_client=None,
        authorization_manager=None,
        tool_registry=None,
        provider_name="p",
        model="m",
        description="读取指定文件并按目标生成修改后的完整文件内容",
    )
    registry = CapabilityRegistry()
    registry.register(agent)
    dispatcher = Dispatcher(get_registry(), registry)

    items = await dispatcher.request_tool_info()
    ids = {item["tool_id"] for item in items}

    # 普通工具仍然在列，agent 工具进入目录并带类型标记
    assert "file_creator" in ids
    assert "coding_agent" in ids
    agent_item = next(item for item in items if item["tool_id"] == "coding_agent")
    assert agent_item["kind"] == "agent_tool"
    assert agent_item["category"] == "agent"
    assert "agent_tool" in agent_item["description"]
    assert "读取指定文件" in agent_item["description"]


@pytest.mark.asyncio
async def test_dispatcher_without_capability_registry_keeps_old_behavior():
    dispatcher = Dispatcher(get_registry())
    items = await dispatcher.request_tool_info()
    assert all(item.get("kind") != "agent_tool" for item in items)


# ===== B. 计划契约校验 =====


def _agent_step(**overrides):
    step = {
        "description": "修改 greet 返回值",
        "tool": "coding_agent",
        "inputs": {
            "path": "src/app.py",
            "instruction": "把 return 1 改成 return 'hello, world'",
            "allowed_tools": ["text_editor"],
        },
        "acceptance": [
            {"type": "content_contains", "path": "src/app.py", "text": "hello, world"}
        ],
    }
    step.update(overrides)
    return step


def _plan(steps):
    return {"segments": [{"description": "seg", "steps": steps}]}


def test_validate_agent_steps_accepts_valid_agent_step():
    ok, err = validate_agent_steps(_plan([_agent_step()]))
    assert ok, err


def test_validate_agent_steps_passes_non_agent_plan():
    plan = _plan([
        {"description": "创建文件", "tool": "file_creator", "inputs": {"path": "a.txt", "content": "x"}}
    ])
    ok, err = validate_agent_steps(plan)
    assert ok, err


def test_validate_agent_steps_missing_path():
    step = _agent_step()
    del step["inputs"]["path"]
    ok, err = validate_agent_steps(_plan([step]))
    assert not ok
    assert "inputs.path" in err


def test_validate_agent_steps_rejects_privilege_escalation_in_allowed_tools():
    step = _agent_step()
    step["inputs"]["allowed_tools"] = ["text_editor", "command_runner"]
    ok, err = validate_agent_steps(_plan([step]))
    assert not ok
    assert "allowed_tools" in err


def test_validate_agent_steps_missing_allowed_tools():
    step = _agent_step()
    del step["inputs"]["allowed_tools"]
    ok, err = validate_agent_steps(_plan([step]))
    assert not ok
    assert "inputs.allowed_tools" in err


def test_validate_agent_steps_requires_acceptance():
    step = _agent_step(acceptance=[])
    ok, err = validate_agent_steps(_plan([step]))
    assert not ok
    assert "acceptance" in err


def test_validate_agent_steps_rejects_unknown_acceptance_type():
    step = _agent_step(acceptance=[{"type": "looks_good_to_me"}])
    ok, err = validate_agent_steps(_plan([step]))
    assert not ok
    assert "acceptance" in err


def test_validate_agent_steps_rejects_directory_path():
    step = _agent_step()
    step["inputs"]["path"] = "src/"
    ok, err = validate_agent_steps(_plan([step]))
    assert not ok
    assert "path" in err


# ===== C/D. 设计器提示词与设计期打回 =====


def _design_document():
    return DesignDocument(
        user_request="修改 src/app.py 的返回值",
        part1_design=DesignPart1(
            architecture=Architecture(components=["app"], interactions=["无"]),
            difficulty=Difficulty(overall="低", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[ToolRequirement(tool_id="coding_agent", availability="available")],
            overall_tool_completeness=1.0,
            risks=[],
        ),
    )


def _make_designer(response: str):
    llm = LLMClient()
    provider = DeterministicProvider(response)
    llm.register_provider(provider)
    config = {"primary": {"provider": "deterministic", "model": "test-model", "temperature": 0.3}}
    designer = DefaultDesigner(llm, config)
    return designer, provider


_VALID_PLAN_JSON = json.dumps(
    {
        "segments": [
            {
                "description": "修改代码",
                "steps": [
                    {
                        "description": "修改 greet 返回值",
                        "tool": "coding_agent",
                        "inputs": {
                            "path": "src/app.py",
                            "instruction": "把 return 1 改成 return 'hello, world'",
                            "allowed_tools": ["text_editor"],
                        },
                        "acceptance": [
                            {"type": "content_contains", "path": "src/app.py", "text": "hello, world"}
                        ],
                    }
                ],
            }
        ],
        "implementation_list": ["src/app.py"],
    },
    ensure_ascii=False,
)


@pytest.mark.asyncio
async def test_plan_prompt_contains_agent_contract():
    designer, provider = _make_designer(_VALID_PLAN_JSON)
    await designer.generate_plan(_design_document())
    # DeterministicProvider 记录了完整 messages；检查计划提示词。
    plan_call_messages = provider.calls[-1]
    prompt_text = "\n".join(m.content for m in plan_call_messages)
    assert "coding_agent" in prompt_text
    assert "coding_agent 步骤契约" in prompt_text
    assert "allowed_tools" in prompt_text
    # 旧的"只使用 file_creator"一刀切白名单必须已被分级约束替换
    assert "只使用 file_creator，不要执行任何 shell 命令" not in prompt_text
    assert "不要执行任何 shell 命令" in prompt_text


@pytest.mark.asyncio
async def test_invalid_agent_step_plan_is_rejected_at_design_time():
    invalid = json.dumps(
        {
            "segments": [
                {
                    "description": "修改代码",
                    "steps": [
                        {
                            "description": "越权修改",
                            "tool": "coding_agent",
                            "inputs": {
                                "path": "src/app.py",
                                "instruction": "x",
                                "allowed_tools": ["command_runner"],
                            },
                            "acceptance": [],
                        }
                    ],
                }
            ],
            "implementation_list": [],
        },
        ensure_ascii=False,
    )
    designer, _ = _make_designer(invalid)
    with pytest.raises(GenerationError, match="agent 契约校验失败"):
        await designer.generate_plan(_design_document())
