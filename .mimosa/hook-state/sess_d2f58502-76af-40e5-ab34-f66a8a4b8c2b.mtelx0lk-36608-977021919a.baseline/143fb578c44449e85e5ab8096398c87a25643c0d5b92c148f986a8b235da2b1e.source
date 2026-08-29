"""
ToolManager AI - 工具管理员

职责：
1. 工具入库评估
2. 工具可用性检查
3. 工具问题确认
"""

from typing import Any, Dict, List

from models import ToolSchema
from utils.interfaces import ToolManagerAI
from llm_client import LLMClient, Message


class DefaultToolManager(ToolManagerAI):
    """默认ToolManager实现"""
    
    def __init__(self, llm_client: LLMClient, model_config: Dict[str, Any]) -> None:
        self.llm_client = llm_client
        self.model_config = model_config
    
    @property
    def name(self) -> str:
        return "toolmanager"
    
    @property
    def version(self) -> str:
        return "0.1.0"
    
    async def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """统一处理入口"""
        action = request.get("action")
        
        if action == "assess_tool":
            result = await self.assess_tool(
                request["tool_id"],
                ToolSchema(**request["tool_schema"]),
            )
            return result
        elif action == "confirm_tool_issue":
            result = await self.confirm_tool_issue(request["tool_id"], request["error"])
            return result
        elif action == "suggest_alternatives":
            alternatives = await self.suggest_alternatives(request["tool_id"])
            return {"alternatives": alternatives}
        else:
            raise ValueError(f"Unknown action: {action}")
    
    async def health_check(self) -> bool:
        return True
    
    async def assess_tool(self, tool_id: str, tool_schema: ToolSchema) -> Dict[str, Any]:
        """评估工具可用性和质量"""
        
        system_prompt = """你是一个工具评估专家。
评估工具的：
1. 可用性（available/unavailable/partial）
2. 质量等级（high/medium/low）
3. 限制条件
4. 潜在风险

返回JSON格式。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"工具：{tool_schema.model_dump_json()}"),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.1,
        )
        
        # TODO: 解析响应
        return {
            "tool_id": tool_id,
            "availability": "available",
            "quality": "high",
            "limitations": [],
            "risks": [],
        }
    
    async def confirm_tool_issue(self, tool_id: str, error: str) -> Dict[str, Any]:
        """确认工具问题"""
        
        system_prompt = """你是一个工具问题诊断专家。
分析错误信息，确认是否是工具问题。
返回JSON格式：{is_tool_issue: bool, reason: str, suggestion: str}"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"工具：{tool_id}\n错误：{error}"),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.1,
        )
        
        # TODO: 解析响应
        return {
            "is_tool_issue": True,
            "reason": "Tool not available",
            "suggestion": "Use alternative tool",
        }
    
    async def suggest_alternatives(self, tool_id: str) -> List[str]:
        """建议替代工具"""
        
        system_prompt = """你是一个工具推荐专家。
根据工具ID，推荐可替代的工具。
返回工具ID列表（JSON数组）。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"工具：{tool_id}"),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.2,
        )
        
        # TODO: 解析响应
        return ["alternative-tool-1", "alternative-tool-2"]
