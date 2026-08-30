"""
Designer 生成失败契约测试。

旧契约：生成失败被静默掩盖——generate_design 返回 components=["待定"] 的占位设计书、
revise_design 原样返回旧设计、generate_plan 返回伪造的 Failed 占位计划，
下游把这些残次品当正常产物继续审批/执行。

新契约：失败必须显式抛出 GenerationError（designer 模块级导出），
成功路径行为不变。
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from designer import DefaultDesigner, GenerationError
from cache import DesignCache, PlanCache
from models import (
    DesignDocument,
    DesignPart1,
    Architecture,
    Difficulty,
    ToolAssessment,
)

MODEL_CONFIG = {
    "primary": {
        "provider": "stub",
        "model": "stub-model",
        "temperature": 0.1,
    }
}


class _ExplodingLLM:
    """chat() 永远抛错的 LLM 桩，记录调用次数。"""

    def __init__(self, exc: Exception | None = None):
        self.calls = 0
        self._exc = exc or RuntimeError("LLM 不可用")

    async def chat(self, **kwargs):
        self.calls += 1
        raise self._exc


def _make_designer(llm, cache_dir: Path):
    """构造 designer，并把文件缓存隔离到测试目录（默认缓存文件是仓库级共享的）。"""
    designer = DefaultDesigner(llm, dict(MODEL_CONFIG), dispatcher=None)
    designer.design_cache = DesignCache(cache_file=str(cache_dir / "design_cache.json"))
    designer.plan_cache = PlanCache(cache_file=str(cache_dir / "plan_cache.json"))
    return designer


def _sample_design() -> DesignDocument:
    return DesignDocument(
        design_id=uuid4(),
        user_request="做一个待办事项应用",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["前端", "后端"], interactions=["HTTP"]),
            difficulty=Difficulty(overall="medium", breakdown={"前端": "easy"}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
    )


# ===== 失败必须抛 GenerationError =====

async def test_generate_design_failure_raises_generation_error(tmp_path):
    """回归：generate_design 曾在 LLM 失败时返回 components=["待定"] 的占位设计书。"""
    llm = _ExplodingLLM()
    designer = _make_designer(llm, tmp_path)

    with pytest.raises(GenerationError, match="设计书生成失败"):
        await designer.generate_design("做一个待办事项应用")


async def test_generate_plan_failure_raises_generation_error_after_retries(tmp_path, monkeypatch):
    """回归：generate_plan 曾在重试耗尽后返回伪造的 Failed 占位计划。

    保留 4 次重试语义（断言恰好调用了 4 次），但把重试等待 sleep 打成空操作让测试瞬时完成。
    """
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    llm = _ExplodingLLM()
    designer = _make_designer(llm, tmp_path)
    design = _sample_design()

    with pytest.raises(GenerationError, match="执行计划生成失败"):
        await designer.generate_plan(design)

    assert llm.calls == 4, "必须保持原有的 4 次尝试（1 次主调用 + 3 次重试）"


async def test_revise_design_failure_raises_generation_error(tmp_path):
    """回归：revise_design 曾在失败时静默返回原设计书，用户以为反馈已生效。"""
    llm = _ExplodingLLM()
    designer = _make_designer(llm, tmp_path)
    original = _sample_design()

    with pytest.raises(GenerationError, match="设计书修订失败"):
        await designer.revise_design(original, "把前端换成 Vue")


async def test_generate_design_failure_wraps_original_cause(tmp_path):
    """raise ... from e：底层异常必须保留在 __cause__ 上。"""
    llm = _ExplodingLLM(RuntimeError("网络断了"))
    designer = _make_designer(llm, tmp_path)

    with pytest.raises(GenerationError) as exc_info:
        await designer.generate_design("需求")
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "网络断了" in str(exc_info.value.__cause__)


# ===== 合理降级保持不变 =====

async def test_tool_info_unavailable_degrades_to_placeholder(tmp_path):
    """dispatcher 获取工具信息失败是合理降级（warning + 占位串），不应导致生成失败。"""
    class _BrokenDispatcher:
        async def request_tool_info(self):
            raise RuntimeError("工具库挂了")

    class _OkLLM:
        async def chat(self, **kwargs):
            return SimpleNamespace(content=json.dumps({
                "architecture": {"components": ["api"], "interactions": ["内部调用"]},
                "difficulty": {"overall": "easy", "breakdown": {}},
                "required_tools": [],
                "overall_tool_completeness": 1.0,
                "risks": [],
            }))

    designer = DefaultDesigner(_OkLLM(), dict(MODEL_CONFIG), dispatcher=_BrokenDispatcher())
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "design_cache.json"))

    design = await designer.generate_design("需求")  # 不应抛 GenerationError
    assert design.part1_design.architecture.components == ["api"]


# ===== 成功路径行为不变 =====

async def test_generate_design_happy_path_unchanged(tmp_path):
    """LLM 返回合法 JSON 时，行为与重构前一致：返回完整 DesignDocument。"""
    class _OkLLM:
        async def chat(self, **kwargs):
            return SimpleNamespace(content=json.dumps({
                "architecture": {
                    "components": ["frontend", "backend"],
                    "interactions": ["REST"],
                },
                "difficulty": {"overall": "medium", "breakdown": {"backend": "hard"}},
                "required_tools": [
                    {
                        "tool_id": "file_creator",
                        "availability": "available",
                        "quality": "high",
                    }
                ],
                "overall_tool_completeness": 0.9,
                "risks": ["风险一"],
            }))

    designer = _make_designer(_OkLLM(), tmp_path)

    design = await designer.generate_design("待办事项应用")
    assert design.user_request == "待办事项应用"
    assert design.part1_design.architecture.components == ["frontend", "backend"]
    assert design.part2_tool_assessment.overall_tool_completeness == 0.9


async def test_generate_plan_happy_path_unchanged(tmp_path):
    """LLM 返回合法计划 JSON 时，行为与重构前一致：返回可执行的 ExecutionPlan。"""
    plan_payload = {
        "segments": [
            {
                "description": "项目初始化",
                "steps": [
                    {
                        "description": "创建项目目录",
                        "tool": "file_creator",
                        "inputs": {"path": "./project", "content": ""},
                    }
                ],
            }
        ],
        "implementation_list": ["src/main.py"],
    }

    class _OkLLM:
        async def chat(self, **kwargs):
            return SimpleNamespace(content=json.dumps(plan_payload, ensure_ascii=False))

    designer = _make_designer(_OkLLM(), tmp_path)
    design = _sample_design()

    plan = await designer.generate_plan(design)
    assert plan.design_id == design.design_id
    assert len(plan.segments) == 1
    assert plan.segments[0].steps[0].tool == "file_creator"
