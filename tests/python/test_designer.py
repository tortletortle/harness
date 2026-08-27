"""
测试Designer AI
"""

import pytest
from uuid import uuid4
from datetime import datetime

from designer import DefaultDesigner
from llm_client import LLMClient
from models import DesignDocument


@pytest.fixture
def designer():
    """创建Designer实例"""
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_generate_design(designer):
    """测试生成设计书（需要真实LLM调用，标记为integration，默认跑pytest -m "not integration"时会跳过）"""
    request = {
        "action": "generate_design",
        "user_request": "创建一个简单的Web应用",
    }

    result = await designer.process(request)

    assert "design" in result
    assert result["design"]["user_request"] == "创建一个简单的Web应用"


# 更多测试...
