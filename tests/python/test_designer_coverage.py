# -*- coding: utf-8 -*-
"""
Designer 覆盖率补齐：process 分发、dispatcher/缓存/记忆分支、tool_calls 循环、
JSON 兜底、修订/修复/计划的重试分支、plan 缓存复用、assess/summary。

全部使用桩 LLM（_ScriptedLLM / DeterministicProvider），离线；缓存与工作区隔离到 tmp。
"""
import asyncio
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from cache import DesignCache, PlanCache
from designer import DefaultDesigner, GenerationError
from llm_client import DeterministicProvider, LLMClient, LLMResponse
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    StepStatus,
    ToolAssessment,
)

MODEL_CONFIG = {
    "primary": {"provider": "stub", "model": "stub-model", "temperature": 0.1}
}

DESIGN_JSON = {
    "architecture": {"components": ["frontend", "backend"], "interactions": ["REST"]},
    "difficulty": {"overall": "medium", "breakdown": {"backend": "hard"}},
    "required_tools": [
        {"tool_id": "file_creator", "availability": "available", "quality": "high"}
    ],
    "overall_tool_completeness": 0.9,
    "risks": ["风险一"],
}

PLAN_JSON = {
    "segments": [
        {
            "description": "项目初始化",
            "steps": [
                {
                    "description": "创建项目目录",
                    "tool": "file_creator",
                    "inputs": {"path": "./project", "content": ""},
                    "acceptance": [
                        {"type": "file_exists", "path": "./project", "kind": "directory"}
                    ],
                }
            ],
        }
    ],
    "implementation_list": ["src/main.py"],
}

REPAIR_JSON = json.dumps(
    {
        "steps": [
            {
                "description": "补配置文件",
                "tool": "file_creator",
                "inputs": {"path": "config/needed.yaml", "content": "key: value"},
                "acceptance": [
                    {"type": "file_exists", "path": "config/needed.yaml", "kind": "file"}
                ],
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _isolated_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))


class _ScriptedLLM:
    """按队列返回响应的 LLM 桩；队列元素为 Exception 时抛错。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        assert self._responses, "LLM 调用次数超出剧本"
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return SimpleNamespace(content=item)
        return item


def _make_designer(llm, tmp_path, dispatcher=None, config=None):
    designer = DefaultDesigner(
        llm, dict(MODEL_CONFIG if config is None else config), dispatcher=dispatcher
    )
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "design_cache.json"))
    designer.plan_cache = PlanCache(cache_file=str(tmp_path / "plan_cache.json"))
    return designer


def _sample_design():
    return DesignDocument(
        design_id=uuid4(),
        user_request="做一个待办事项应用",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["前端", "后端"], interactions=["HTTP"]),
            difficulty=Difficulty(overall="medium", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
    )


def _sample_plan(design):
    step = Step(
        step_id=uuid4(), order=1, description="第一步", tool="file_creator",
        inputs={"path": "a.txt", "content": "a"}, check_criteria="手动检查",
    )
    return (
        ExecutionPlan(
            plan_id=uuid4(), design_id=design.design_id, status=PlanStatus.PENDING,
            created_at=datetime.utcnow(),
            segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[step])],
            implementation_list=[],
        ),
        step,
    )


def _no_sleep_fixture(monkeypatch):
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


# ===== 基础属性 =====

def test_name_and_version(tmp_path):
    designer = _make_designer(_ScriptedLLM([]), tmp_path)
    assert designer.name == "designer"
    # 版本随 python/version.py 单一来源走，不再硬编码具体值
    from version import __version__
    assert designer.version == __version__


# ===== process() 分发 =====

@pytest.mark.asyncio
async def test_process_generate_design_success(tmp_path):
    designer = _make_designer(_ScriptedLLM([]), tmp_path)
    design = _sample_design()
    designer.generate_design = AsyncMock(return_value=design)
    result = await designer.process({"action": "generate_design", "user_request": "x"})
    assert result["design"]["user_request"] == design.user_request


@pytest.mark.asyncio
async def test_process_generate_plan_success(tmp_path):
    designer = _make_designer(_ScriptedLLM([]), tmp_path)
    design = _sample_design()
    plan, _ = _sample_plan(design)
    designer.generate_plan = AsyncMock(return_value=plan)
    result = await designer.process(
        {"action": "generate_plan", "design": design.model_dump()}
    )
    assert result["plan"]["design_id"] == design.design_id


@pytest.mark.asyncio
async def test_process_assess_severity_success(tmp_path):
    designer = _make_designer(_ScriptedLLM([]), tmp_path)
    designer.assess_severity = AsyncMock(return_value=3)
    result = await designer.process(
        {"action": "assess_severity", "issue": "boom", "context": {"a": 1}}
    )
    assert result == {"severity": 3}


@pytest.mark.asyncio
async def test_process_generate_summary_success(tmp_path):
    designer = _make_designer(_ScriptedLLM([]), tmp_path)
    design = _sample_design()
    plan, step = _sample_plan(design)
    designer.generate_summary = AsyncMock(return_value="总结")
    result = await designer.process(
        {
            "action": "generate_summary",
            "plan": plan.model_dump(),
            "results": [step.model_dump()],
        }
    )
    assert result == {"summary": "总结"}


@pytest.mark.asyncio
async def test_process_unknown_action_raises(tmp_path):
    designer = _make_designer(_ScriptedLLM([]), tmp_path)
    with pytest.raises(ValueError, match="Unknown action"):
        await designer.process({"action": "nope"})


# ===== generate_design：缓存命中 + 无 primary 配置 =====

@pytest.mark.asyncio
async def test_generate_design_cache_hit_without_primary_config(tmp_path):
    """model_config 缺 primary 时指纹降级为空配置；缓存命中直接返回，不调 LLM。"""
    llm = _ScriptedLLM([])
    designer = _make_designer(llm, tmp_path, config={})
    design = _sample_design()
    fingerprint = hashlib.md5("（未配置dispatcher）||".encode()).hexdigest()
    designer.design_cache.set(
        "req-xyz", design.model_dump(mode="json"), fingerprint=fingerprint
    )
    got = await designer.generate_design("req-xyz")
    assert got.design_id == design.design_id
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_generate_design_memory_injection_failure_is_fail_open(tmp_path, monkeypatch):
    """记忆注入失败只降级（warning），不阻断设计生成。"""
    import knowledge.injection as injection

    def boom(_request):
        raise RuntimeError("索引坏了")

    monkeypatch.setattr(injection, "build_design_memory_block", boom)
    designer = _make_designer(
        _ScriptedLLM([json.dumps(DESIGN_JSON, ensure_ascii=False)]), tmp_path
    )
    design = await designer.generate_design("需求")
    assert design.part1_design.architecture.components == ["frontend", "backend"]


# ===== generate_design：tool_calls 循环 =====

class _SearchDispatcher:
    def __init__(self, search_error=None):
        self.search_calls = []
        self._error = search_error

    async def request_tool_info(self):
        return [
            {
                "tool_id": "super_search",
                "category": "search",
                "description": "多引擎搜索",
            }
        ]

    async def request_search(self, query, context=None):
        self.search_calls.append((query, context))
        if self._error:
            raise self._error
        return {"hits": ["r1"]}


def _tool_calls_response():
    return LLMResponse(
        content="",
        model="m",
        tool_calls=[
            {
                "id": "c1",
                "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "q1"}),
                },
            },
            {"id": "c2", "function": {"name": "evil_tool", "arguments": "{}"}},
        ],
    )


@pytest.mark.asyncio
async def test_generate_design_tool_calls_loop(tmp_path):
    """web_search 走 dispatcher（含 Grant 上下文），非搜索工具被拒绝，最终产出设计书。"""
    dispatcher = _SearchDispatcher()
    designer = _make_designer(
        _ScriptedLLM([
            _tool_calls_response(),
            LLMResponse(content="thinking", model="m"),
            json.dumps(DESIGN_JSON, ensure_ascii=False),
        ]),
        tmp_path,
        dispatcher=dispatcher,
    )
    design = await designer.generate_design("需求", search_grant_id="g1")
    assert design.part2_tool_assessment.overall_tool_completeness == 0.9
    assert dispatcher.search_calls == [("q1", {"grant_id": "g1"})]


@pytest.mark.asyncio
async def test_generate_design_search_error_becomes_tool_message(tmp_path):
    """搜索异常转为 tool 错误消息，不中断设计流程。"""
    dispatcher = _SearchDispatcher(search_error=RuntimeError("断网"))
    designer = _make_designer(
        _ScriptedLLM([
            _tool_calls_response(),
            LLMResponse(content="thinking", model="m"),
            json.dumps(DESIGN_JSON, ensure_ascii=False),
        ]),
        tmp_path,
        dispatcher=dispatcher,
    )
    design = await designer.generate_design("需求")
    assert design.user_request == "需求"
    assert dispatcher.search_calls == [("q1", None)]


# ===== generate_design：非 JSON 重试与 markdown 解包 =====

@pytest.mark.asyncio
async def test_generate_design_non_json_retry(tmp_path):
    designer = _make_designer(
        _ScriptedLLM([
            "这是一些散文，不是JSON",
            json.dumps(DESIGN_JSON, ensure_ascii=False),
        ]),
        tmp_path,
    )
    design = await designer.generate_design("需求")
    assert design.part1_design.difficulty.overall == "medium"


@pytest.mark.asyncio
async def test_generate_design_json_fenced_response(tmp_path):
    designer = _make_designer(
        _ScriptedLLM(["```json\n" + json.dumps(DESIGN_JSON) + "\n```"]),
        tmp_path,
    )
    design = await designer.generate_design("需求")
    assert design.part2_tool_assessment.risks == ["风险一"]


@pytest.mark.asyncio
async def test_generate_design_bare_fenced_response(tmp_path):
    designer = _make_designer(
        _ScriptedLLM(["```\n" + json.dumps(DESIGN_JSON) + "\n```"]),
        tmp_path,
    )
    design = await designer.generate_design("需求")
    assert design.user_request == "需求"


# ===== revise_design =====

@pytest.mark.asyncio
async def test_revise_design_with_dispatcher(tmp_path):
    dispatcher = _SearchDispatcher()
    designer = _make_designer(
        _ScriptedLLM([json.dumps(DESIGN_JSON, ensure_ascii=False)]),
        tmp_path,
        dispatcher=dispatcher,
    )
    revised = await designer.revise_design(_sample_design(), "换个方案")
    assert revised.user_comments == "换个方案"
    assert revised.part1_design.architecture.components == ["frontend", "backend"]


@pytest.mark.asyncio
async def test_revise_design_dispatcher_failure_degrades(tmp_path):
    class _BrokenDispatcher:
        async def request_tool_info(self):
            raise RuntimeError("工具库挂了")

    designer = _make_designer(
        _ScriptedLLM([json.dumps(DESIGN_JSON, ensure_ascii=False)]),
        tmp_path,
        dispatcher=_BrokenDispatcher(),
    )
    revised = await designer.revise_design(_sample_design(), "换个方案")
    assert revised.user_comments == "换个方案"


@pytest.mark.asyncio
async def test_revise_design_json_fenced_response(tmp_path):
    designer = _make_designer(
        _ScriptedLLM(["说明：```json\n" + json.dumps(DESIGN_JSON) + "\n```"]),
        tmp_path,
    )
    revised = await designer.revise_design(_sample_design(), "fb")
    assert revised.part1_design.difficulty.overall == "medium"


@pytest.mark.asyncio
async def test_revise_design_prose_wrapped_json(tmp_path):
    designer = _make_designer(
        _ScriptedLLM(["修订完成：" + json.dumps(DESIGN_JSON) + "（完毕）"]),
        tmp_path,
    )
    revised = await designer.revise_design(_sample_design(), "fb")
    assert revised.part2_tool_assessment.overall_tool_completeness == 0.9


# ===== generate_repair_steps 重试分支 =====

_FAILED_STEP = {"description": "跑命令", "tool": "command_runner", "inputs": {"command": "x"}}


@pytest.mark.asyncio
async def test_repair_steps_json_parse_failure_retries(tmp_path):
    designer = _make_designer(_ScriptedLLM(["garbage {{[", REPAIR_JSON]), tmp_path)
    steps = await designer.generate_repair_steps(_FAILED_STEP, "失败原因")
    assert steps[0]["tool"] == "file_creator"


@pytest.mark.asyncio
async def test_repair_steps_bad_count_retries(tmp_path):
    designer = _make_designer(
        _ScriptedLLM([json.dumps({"steps": []}), REPAIR_JSON]), tmp_path
    )
    steps = await designer.generate_repair_steps(_FAILED_STEP, "失败原因")
    assert len(steps) == 1


@pytest.mark.asyncio
async def test_repair_steps_tool_contract_failure_retries(tmp_path):
    bad = json.dumps({
        "steps": [{
            "description": "跑测试",
            "tool": "test_runner",
            "inputs": {},
            "acceptance": [{"type": "exit_code", "equals": 0}],
        }]
    })
    designer = _make_designer(_ScriptedLLM([bad, REPAIR_JSON]), tmp_path)
    steps = await designer.generate_repair_steps(_FAILED_STEP, "失败原因")
    assert steps[0]["tool"] == "file_creator"


@pytest.mark.asyncio
async def test_repair_steps_attempt_exception_retries(tmp_path):
    designer = _make_designer(
        _ScriptedLLM([RuntimeError("boom"), REPAIR_JSON]), tmp_path
    )
    steps = await designer.generate_repair_steps(_FAILED_STEP, "失败原因")
    assert steps[0]["description"] == "补配置文件"


# ===== generate_plan：缓存复用 =====

_PLAN_WITH_DEPS = {
    "segments": [
        {
            "description": "两步",
            "steps": [
                {
                    "step_id": "step-1",
                    "description": "第一步",
                    "tool": "file_creator",
                    "inputs": {"path": "a.txt", "content": "a"},
                },
                {
                    "step_id": "step-2",
                    "description": "第二步",
                    "tool": "file_creator",
                    "inputs": {"path": "b.txt", "content": "b"},
                    "depends_on": ["step-1"],
                },
            ],
        }
    ],
    "implementation_list": [],
}


@pytest.mark.asyncio
async def test_generate_plan_cache_hit_remaps_ids(tmp_path):
    """缓存命中复用结构但换掉 plan_id/step_id，depends_on 同步重映射。"""
    designer = _make_designer(
        _ScriptedLLM([json.dumps(_PLAN_WITH_DEPS, ensure_ascii=False)]), tmp_path
    )
    design = _sample_design()
    first = await designer.generate_plan(design)
    assert designer.plan_cache.get(str(design.design_id)) is not None

    second = await designer.generate_plan(design)  # 命中缓存，不再调 LLM
    assert designer.llm_client.calls == 1
    assert second.plan_id != first.plan_id
    assert second.design_id == design.design_id
    new_ids = [str(s.step_id) for s in second.segments[0].steps]
    old_ids = [str(s.step_id) for s in first.segments[0].steps]
    assert set(new_ids).isdisjoint(old_ids)
    assert second.segments[0].steps[1].depends_on == [new_ids[0]]
    assert all(s.status == StepStatus.PENDING for s in second.segments[0].steps)


@pytest.mark.asyncio
async def test_generate_plan_cache_hit_invalid_graph_raises(tmp_path):
    designer = _make_designer(
        _ScriptedLLM([json.dumps(_PLAN_WITH_DEPS, ensure_ascii=False)]), tmp_path
    )
    design = _sample_design()
    await designer.generate_plan(design)
    cached = designer.plan_cache.get(str(design.design_id))
    cached["segments"][0]["steps"][1]["depends_on"] = ["ghost-id"]
    designer.plan_cache.set(str(design.design_id), cached)
    with pytest.raises(GenerationError, match="图校验失败"):
        await designer.generate_plan(design)


# ===== generate_plan：重试分支 =====

@pytest.mark.asyncio
async def test_generate_plan_with_fallback_config(tmp_path):
    config = dict(MODEL_CONFIG)
    config["fallback"] = {
        "provider": "stub", "model": "stub-fb", "temperature": 0.2,
    }
    designer = _make_designer(
        _ScriptedLLM([json.dumps(PLAN_JSON, ensure_ascii=False)]),
        tmp_path, config=config,
    )
    plan = await designer.generate_plan(_sample_design())
    assert len(plan.segments) == 1


@pytest.mark.asyncio
async def test_generate_plan_retries_reuse_last_strategy(tmp_path, monkeypatch):
    """无 fallback 配置时策略不足 4 条，第 4 次复用最后一条；空响应触发重试。"""
    _no_sleep_fixture(monkeypatch)
    designer = _make_designer(
        _ScriptedLLM(["", "", "", json.dumps(PLAN_JSON, ensure_ascii=False)]),
        tmp_path,
    )
    plan = await designer.generate_plan(_sample_design())
    assert designer.llm_client.calls == 4
    assert plan.segments[0].steps[0].tool == "file_creator"


@pytest.mark.asyncio
async def test_generate_plan_short_invalid_json_retries(tmp_path, monkeypatch):
    _no_sleep_fixture(monkeypatch)
    designer = _make_designer(
        _ScriptedLLM(["xxx", json.dumps(PLAN_JSON, ensure_ascii=False)]),
        tmp_path,
    )
    plan = await designer.generate_plan(_sample_design())
    assert designer.llm_client.calls == 2
    assert plan.implementation_list == ["src/main.py"]


@pytest.mark.asyncio
async def test_generate_plan_tool_contract_failure_retries(tmp_path, monkeypatch):
    _no_sleep_fixture(monkeypatch)
    bad = json.dumps({
        "segments": [{
            "description": "s",
            "steps": [{
                "description": "跑测试",
                "tool": "test_runner",
                "inputs": {},
                "acceptance": [{"type": "exit_code", "equals": 0}],
            }],
        }]
    })
    designer = _make_designer(
        _ScriptedLLM([bad, json.dumps(PLAN_JSON, ensure_ascii=False)]),
        tmp_path,
    )
    plan = await designer.generate_plan(_sample_design())
    assert designer.llm_client.calls == 2
    assert plan.segments[0].steps[0].tool == "file_creator"


@pytest.mark.asyncio
async def test_generate_plan_invalid_graph_retries(tmp_path, monkeypatch):
    """自依赖计划通过契约但 WorkGraph 校验失败 → 重试后成功。"""
    _no_sleep_fixture(monkeypatch)
    bad = json.dumps({
        "segments": [{
            "description": "s",
            "steps": [{
                "step_id": "a",
                "description": "自依赖",
                "tool": "file_creator",
                "inputs": {"path": "a.txt"},
                "depends_on": ["a"],
            }],
        }]
    })
    designer = _make_designer(
        _ScriptedLLM([bad, json.dumps(PLAN_JSON, ensure_ascii=False)]),
        tmp_path,
    )
    plan = await designer.generate_plan(_sample_design())
    assert designer.llm_client.calls == 2
    assert plan.segments[0].steps[0].description == "创建项目目录"


@pytest.mark.asyncio
async def test_generate_plan_normalizes_step_fields(tmp_path):
    """outputs/inputs/acceptance 非标准形态被规范化，不抛异常。"""
    payload = {
        "segments": [{
            "description": "s",
            "steps": [
                {
                    "description": "输出是字符串",
                    "tool": "file_creator",
                    "inputs": {"path": "a.txt", "content": "x"},
                    "outputs": "oops",
                },
                {
                    "description": "输出是数字",
                    "tool": "file_creator",
                    "inputs": {"path": "b.txt", "content": "y"},
                    "outputs": 42,
                },
                {
                    "description": "输入是字符串",
                    "tool": "file_creator",
                    "inputs": "not-a-dict",
                },
                {
                    "description": "验收不是列表",
                    "tool": "file_creator",
                    "inputs": {"path": "d.txt", "content": "z"},
                    "acceptance": {"type": "file_exists"},
                },
            ],
        }]
    }
    designer = _make_designer(
        _ScriptedLLM([json.dumps(payload, ensure_ascii=False)]), tmp_path
    )
    plan = await designer.generate_plan(_sample_design())
    steps = plan.segments[0].steps
    # outputs 规范化发生在构建期（Step.outputs 统一为 None，见 outputs=None 注释）
    assert steps[2].inputs == {"value": "not-a-dict"}
    assert steps[3].acceptance == []
    assert steps[3].check_criteria == "手动检查"


# ===== assess_severity / generate_summary =====

def _deterministic_designer(text, tmp_path):
    llm = LLMClient()
    llm.register_provider(DeterministicProvider(text))
    config = {
        "primary": {
            "provider": "deterministic", "model": "test", "temperature": 0.1,
        }
    }
    return _make_designer(llm, tmp_path, config=config)


@pytest.mark.asyncio
async def test_assess_severity_returns_placeholder(tmp_path):
    designer = _deterministic_designer("3", tmp_path)
    assert await designer.assess_severity("boom", {"k": "v"}) == 2


@pytest.mark.asyncio
async def test_generate_summary_returns_content(tmp_path):
    designer = _deterministic_designer("总结文本", tmp_path)
    design = _sample_design()
    plan, step = _sample_plan(design)
    assert await designer.generate_summary(plan, [step]) == "总结文本"

