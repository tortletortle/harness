"""
测试Designer AI
"""

import pytest
from uuid import uuid4
from datetime import datetime

from python.designer import DefaultDesigner
from python.llm_client import LLMClient
from python.models import DesignDocument


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


@pytest.mark.asyncio
async def test_process_generate_design(designer):
    """测试生成设计书"""
    request = {
        "action": "generate_design",
        "user_request": "创建一个简单的Web应用",
    }
    
    result = await designer.process(request)
    
    assert "design" in result
    assert result["design"]["user_request"] == "创建一个简单的Web应用"


# 更多测试...
