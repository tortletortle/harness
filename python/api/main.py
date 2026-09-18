"""
FastAPI主服务 - Python AI服务入口

提供REST API给Rust层调用
"""

import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from version import __version__ as EAS_VERSION

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from designer import DefaultDesigner
from toolmanager import DefaultToolManager
from knowledge import DefaultKnowledge
from executor import DefaultExecutor
from llm_client import LLMClient, OpenAICompatibleProvider


# 全局状态
app_state: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    await initialize_services()
    yield
    # 关闭时清理
    await cleanup_services()


app = FastAPI(
    title="EAS 2.0 Python API",
    version=EAS_VERSION,
    lifespan=lifespan,
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID（P2 可观测性）：与 api/server.py 相同的贯穿策略
from correlation import new_correlation_id, set_correlation_id  # noqa: E402


@app.middleware("http")
async def correlation_id_middleware(request, call_next):
    incoming = request.headers.get("X-Correlation-ID", "").strip()
    cid = incoming[:64] if incoming and incoming.isprintable() else new_correlation_id()
    set_correlation_id(cid)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response


async def initialize_services() -> None:
    """初始化所有AI服务"""
    
    # 加载配置
    config_path = os.getenv("EAS_CONFIG_PATH", "config/models.yaml")
    # 如果路径不存在，尝试从父目录查找
    if not os.path.exists(config_path):
        config_path = os.path.join("..", config_path)
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 初始化LLM客户端
    llm_client = LLMClient()
    
    # 注册providers - 从配置文件读取
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    # 注册所有配置的provider
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            base_url = base_urls[provider_name]
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_url)
            )
            print(f"[OK] 注册provider: {provider_name}")
    
    # 初始化AI服务
    app_state["llm_client"] = llm_client
    app_state["designer"] = DefaultDesigner(llm_client, config["models"]["designer"])
    app_state["toolmanager"] = DefaultToolManager(llm_client, config["models"]["toolmanager"])
    app_state["knowledge"] = DefaultKnowledge(llm_client, config["models"]["knowledge"])
    app_state["executor"] = DefaultExecutor(llm_client, config["models"]["executor"])
    
    print("[OK] All services initialized")


async def cleanup_services() -> None:
    """清理资源"""
    print("🔄 Cleaning up services...")
    app_state.clear()


@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "EAS 2.0 Python API",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    health_status = {
        "status": "ok",
        "services": {},
    }
    
    for service_name in ["designer", "toolmanager", "knowledge", "executor"]:
        service = app_state.get(service_name)
        if service:
            is_healthy = await service.health_check()
            health_status["services"][service_name] = "ok" if is_healthy else "error"
        else:
            health_status["services"][service_name] = "not_initialized"
    
    return health_status


class ServiceRequest(BaseModel):
    """通用服务请求"""
    action: str
    data: Dict[str, Any]


@app.post("/api/v1/designer")
async def call_designer(request: ServiceRequest):
    """调用Designer AI"""
    designer = app_state.get("designer")
    if not designer:
        raise HTTPException(status_code=503, detail="Designer service not available")
    
    try:
        result = await designer.process({"action": request.action, **request.data})
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/toolmanager")
async def call_toolmanager(request: ServiceRequest):
    """调用ToolManager AI"""
    toolmanager = app_state.get("toolmanager")
    if not toolmanager:
        raise HTTPException(status_code=503, detail="ToolManager service not available")
    
    try:
        result = await toolmanager.process({"action": request.action, **request.data})
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/knowledge")
async def call_knowledge(request: ServiceRequest):
    """调用Knowledge AI"""
    knowledge = app_state.get("knowledge")
    if not knowledge:
        raise HTTPException(status_code=503, detail="Knowledge service not available")
    
    try:
        result = await knowledge.process({"action": request.action, **request.data})
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/executor")
async def call_executor(request: ServiceRequest):
    """调用Executor"""
    executor = app_state.get("executor")
    if not executor:
        raise HTTPException(status_code=503, detail="Executor service not available")
    
    try:
        result = await executor.process({"action": request.action, **request.data})
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=9000,
        reload=True,
        log_level="info",
    )
