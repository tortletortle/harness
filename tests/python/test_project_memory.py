# -*- coding: utf-8 -*-
"""项目工作记忆：计划提示词注入的回归测试。"""

import pytest

from designer import DefaultDesigner
from llm_client import LLMClient
from models import DesignDocument

# 复用 test_full_flow_design 的构造器（同目录，pytest 已加入 sys.path）
from test_full_flow_design import _VALID_PLAN_JSON, _design_document, _make_designer  # noqa: F401


def test_generate_plan_injects_project_memory_into_prompt():
    """project_memory 非空时，计划提示词必须包含记忆段（让计划"接着干"）。"""
    designer, provider = _make_designer(_VALID_PLAN_JSON)

    async def run():
        await designer.generate_plan(_design_document(), project_memory="上次执行：步骤5 失败（参数顺序错误），service.py 已适配")

    import asyncio

    asyncio.run(run())
    plan_call = provider.calls[-1]
    prompt_text = "\n".join(m.content for m in plan_call)
    assert "项目工作记忆" in prompt_text
    assert "上次执行：步骤5 失败" in prompt_text


def test_generate_plan_without_memory_keeps_prompt_clean():
    """未提供记忆时，提示词不含记忆段（向后兼容）。"""
    designer, provider = _make_designer(_VALID_PLAN_JSON)

    async def run():
        await designer.generate_plan(_design_document())

    import asyncio

    asyncio.run(run())
    plan_call = provider.calls[-1]
    prompt_text = "\n".join(m.content for m in plan_call)
    assert "项目工作记忆" not in prompt_text
