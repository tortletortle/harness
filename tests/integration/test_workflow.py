"""
集成测试 - 完整流程
"""

import pytest
import httpx


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
