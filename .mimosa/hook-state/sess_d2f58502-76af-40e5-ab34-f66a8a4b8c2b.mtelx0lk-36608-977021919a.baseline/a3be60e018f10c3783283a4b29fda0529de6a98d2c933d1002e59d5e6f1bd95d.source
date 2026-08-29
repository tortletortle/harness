"""
测试Designer AI
"""

import pytest

from designer import DefaultDesigner, GenerationError
from llm_client import LLMClient


@pytest.fixture
def designer():
    """创建Designer实例（未注册任何 LLM provider）"""
    llm_client = LLMClient()
    config = {
        "primary": {
            "provider": "anthropic",
            "model": "claude-opus-5",
            "temperature": 0.3,
        }
    }
    return DefaultDesigner(llm_client, config)


@pytest.mark.asyncio
async def test_health_check(designer):
    """测试健康检查"""
    result = await designer.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_process_generate_design_fails_loud_without_llm(designer):
    """LLM 不可用时 process(generate_design) 必须显式抛 GenerationError。

    旧契约回归：generate_design 曾在 LLM 失败（此处为 Provider 未注册）时
    静默返回 components=["待定"] 的占位设计书，本测试当时断言占位设计书的
    user_request 也能通过。新契约下失败必须显式暴露，故改为断言 GenerationError。
    """
    request = {
        "action": "generate_design",
        "user_request": "创建一个简单的Web应用",
    }

    with pytest.raises(GenerationError, match="设计书生成失败"):
        await designer.process(request)
