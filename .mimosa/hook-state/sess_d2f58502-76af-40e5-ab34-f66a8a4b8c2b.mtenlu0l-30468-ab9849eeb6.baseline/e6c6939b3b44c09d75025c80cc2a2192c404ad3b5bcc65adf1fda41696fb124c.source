"""
Knowledge AI - 知识助手

职责：
1. 回答用户问题
2. 分析错误
3. 建议修复方案
"""

from typing import Any, Dict, Optional

from models import Step
from utils.interfaces import KnowledgeAI
from llm_client import LLMClient, Message


class DefaultKnowledge(KnowledgeAI):
    """默认Knowledge AI实现"""
    
    def __init__(self, llm_client: LLMClient, model_config: Dict[str, Any]) -> None:
        self.llm_client = llm_client
        self.model_config = model_config
    
    @property
    def name(self) -> str:
        return "knowledge"
    
    @property
    def version(self) -> str:
        return "0.1.0"
    
    async def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """统一处理入口"""
        action = request.get("action")
        
        if action == "query":
            answer = await self.query(request["question"], request.get("context"))
            return {"answer": answer}
        elif action == "analyze_error":
            analysis = await self.analyze_error(request["error"], Step(**request["step"]))
            return analysis
        elif action == "suggest_fix":
            suggestion = await self.suggest_fix(request["issue"], request["context"])
            return {"suggestion": suggestion}
        else:
            raise ValueError(f"Unknown action: {action}")
    
    async def health_check(self) -> bool:
        return True
    
    async def query(self, question: str, context: Optional[Dict[str, Any]] = None) -> str:
        """回答问题"""
        
        system_prompt = """你是一个知识助手，帮助用户理解和解决问题。
提供清晰、准确的答案。"""
        
        user_content = f"问题：{question}"
        if context:
            user_content += f"\n上下文：{context}"
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_content),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.2,
        )
        
        return response.content
    
    async def analyze_error(self, error: str, step: Step) -> Dict[str, Any]:
        """分析错误"""
        
        system_prompt = """你是一个错误分析专家。
分析错误信息和步骤，找出根本原因。
返回JSON格式：{cause: str, category: str, severity: str}"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(
                role="user",
                content=f"错误：{error}\n步骤：{step.model_dump_json()}",
            ),
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
            "cause": "Unknown error",
            "category": "tool_error",
            "severity": "medium",
        }
    
    async def suggest_fix(self, issue: str, context: Dict[str, Any]) -> str:
        """建议修复方案"""
        
        system_prompt = """你是一个问题解决专家。
根据问题和上下文，提供具体的修复建议。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"问题：{issue}\n上下文：{context}"),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.3,
        )
        
        return response.content
