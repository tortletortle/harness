# -*- coding: utf-8 -*-
"""B 验收发现的回归防护：真实 LLM 在步骤里带 step_id 时，
generate_plan 的依赖重映射路径必须正常（此前 NameError: UUID 未定义）。"""
import pytest

from designer import DefaultDesigner
from llm_client import DeterministicProvider, LLMClient


PLAN_WITH_STEP_IDS = """{
  "segments": [
    {
      "description": "两步依赖链",
      "steps": [
        {
          "step_id": "step-1",
          "description": "创建配置",
          "tool": "file_creator",
          "inputs": {"path": "config.yaml", "content": "key: value"},
          "acceptance": [{"type": "file_exists", "path": "config.yaml", "kind": "file"}]
        },
        {
          "step_id": "step-2",
          "depends_on": ["step-1"],
          "description": "写主程序",
          "tool": "file_creator",
          "inputs": {"path": "main.py", "content": "print('ok')"},
          "acceptance": [{"type": "file_exists", "path": "main.py", "kind": "file"}]
        }
      ]
    }
  ],
  "implementation_list": ["config.yaml", "main.py"]
}"""


def _make_designer(response: str):
    llm = LLMClient()
    llm.register_provider(DeterministicProvider(response))
    config = {"primary": {"provider": "deterministic", "model": "test-model", "temperature": 0.3}}
    return DefaultDesigner(llm, config, None)


@pytest.mark.asyncio
async def test_generate_plan_with_llm_step_ids_and_depends_on():
    """LLM 提供 step_id + depends_on：映射到运行时 UUID，依赖改写一致。"""
    designer = _make_designer(PLAN_WITH_STEP_IDS)
    design = _design()

    plan = await designer.generate_plan(design)

    steps = [s for seg in plan.segments for s in seg.steps]
    assert len(steps) == 2
    step1, step2 = steps
    # step_id 是合法 UUID（NameError 回归即在此暴露）
    assert str(step1.step_id) and str(step2.step_id)
    # depends_on 从 LLM 符号 id 改写为 step-1 的运行时 UUID
    assert step2.depends_on == [str(step1.step_id)]
    assert step1.depends_on == []


def test_generate_plan_without_llm_step_ids_unchanged():
    """LLM 不提供 step_id（旧形态）：仍正常生成，依赖为空。"""
    legacy = """{"segments": [{"description": "单步", "steps": [
        {"description": "建文件", "tool": "file_creator",
         "inputs": {"path": "a.txt", "content": "a"},
         "acceptance": [{"type": "file_exists", "path": "a.txt", "kind": "file"}]}
    ]}], "implementation_list": ["a.txt"]}"""
    designer = _make_designer(legacy)
    plan = _run(designer)
    steps = [s for seg in plan.segments for s in seg.steps]
    assert len(steps) == 1
    assert steps[0].depends_on == []


def _design():
    from datetime import datetime

    from models import (
        Architecture,
        DesignDocument,
        DesignPart1,
        Difficulty,
        ToolAssessment,
    )

    return DesignDocument(
        design_id=__import__("uuid").uuid4(),
        user_request="生成计划",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
        user_approval=True,
    )


def _run(designer):
    design = _design()
    import asyncio

    return asyncio.run(designer.generate_plan(design))
