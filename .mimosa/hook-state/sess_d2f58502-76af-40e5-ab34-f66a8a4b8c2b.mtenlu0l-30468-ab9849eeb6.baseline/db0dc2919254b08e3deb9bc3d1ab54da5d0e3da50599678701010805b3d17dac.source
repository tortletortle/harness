"""
集成测试 - 完整流程

注意：本文件针对早期架构（独立 Rust server 监听 8090，
以及 /api/v1/* 路由）。当前实际服务是 python/api/server.py，
路由为 /api/plan/* 等，且需要 Bearer token 认证。
本文件与当前架构不匹配，标记为 integration 且默认 skip，
避免假阳性失败干扰真实回归。若未来重建该集成测试，
应改为直接命中 python/api/server.py 实际路由并带上认证头。
"""

import pytest
import httpx

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(reason="靶对旧架构路由(/api/v1/*, 独立Rust server:8090)，与当前 api/server.py 不匹配"),
]


BASE_URL_PYTHON = "http://localhost:9000"
BASE_URL_RUST = "http://localhost:8090"


@pytest.mark.asyncio
async def test_full_workflow():
    """测试完整工作流程"""
    
    async with httpx.AsyncClient() as client:
        # 1. 检查Python API健康状态
        response = await client.get(f"{BASE_URL_PYTHON}/health")
        assert response.status_code == 200
        
        # 2. 检查Rust Server健康状态
        response = await client.get(f"{BASE_URL_RUST}/health")
        assert response.status_code == 200
        
        # 3. 生成设计书
        design_request = {
            "action": "generate_design",
            "data": {
                "user_request": "创建一个简单的待办事项应用"
            }
        }
        
        response = await client.post(
            f"{BASE_URL_PYTHON}/api/v1/designer",
            json=design_request
        )
        assert response.status_code == 200
        design_result = response.json()
        assert design_result["success"] is True
        
        # 4. 验证设计书（通过Rust层）
        design = design_result["result"]["design"]
        validate_request = {"design": design}
        
        response = await client.post(
            f"{BASE_URL_RUST}/api/v1/design/validate",
            json=validate_request
        )
        assert response.status_code == 200
        
        # 5. 生成执行计划
        plan_request = {
            "action": "generate_plan",
            "data": {
                "design": design
            }
        }
        
        response = await client.post(
            f"{BASE_URL_PYTHON}/api/v1/designer",
            json=plan_request
        )
        assert response.status_code == 200
        plan_result = response.json()
        assert plan_result["success"] is True


@pytest.mark.asyncio
async def test_knowledge_query():
    """测试Knowledge AI查询"""
    
    async with httpx.AsyncClient() as client:
        request = {
            "action": "query",
            "data": {
                "question": "如何修复数据库连接错误？",
                "context": {}
            }
        }
        
        response = await client.post(
            f"{BASE_URL_PYTHON}/api/v1/knowledge",
            json=request
        )
        assert response.status_code == 200
        result = response.json()
        assert "answer" in result["result"]


# 运行前需要启动服务：
# python -m uvicorn python.api.main:app --port 9000
# cargo run --bin eas-server
