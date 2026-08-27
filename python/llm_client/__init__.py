"""
LLM客户端 - 统一的LLM调用接口

支持多个provider（newapi, bbapi, hejuapi, stepfun等），插件化设计
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
import httpx
import json


class Message(BaseModel):
    """消息"""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_call_id: Optional[str] = None  # 用于tool角色的消息


class LLMResponse(BaseModel):
    """LLM响应"""
    content: str
    model: str
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None  # 工具调用请求


class LLMProvider(ABC):
    """LLM Provider接口 - 支持插件化"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider名称"""
        pass
    
    @abstractmethod
    async def chat(
        self,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> LLMResponse:
        """聊天接口"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass


class AnthropicProvider(LLMProvider):
    """Anthropic Claude Provider"""
    
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        try:
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")
    
    @property
    def name(self) -> str:
        return "anthropic"
    
    async def chat(
        self,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用Claude API"""
        # 转换消息格式
        system_message = None
        api_messages = []
        
        for msg in messages:
            if msg.role == "system":
                system_message = msg.content
            else:
                api_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })
        
        # 调用API
        try:
            response = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_message if system_message else None,
                messages=api_messages,
            )
            
            return LLMResponse(
                content=response.content[0].text,
                model=model,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                finish_reason=response.stop_reason,
            )
        except Exception as e:
            raise Exception(f"Anthropic API error: {str(e)}")
    
    async def health_check(self) -> bool:
        try:
            # 简单的健康检查：调用API
            await self.chat(
                messages=[Message(role="user", content="test")],
                model="claude-3-haiku-20240307",
                max_tokens=10,
            )
            return True
        except:
            return False


class OpenAIProvider(LLMProvider):
    """OpenAI GPT Provider"""
    
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(api_key=api_key)
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")
    
    @property
    def name(self) -> str:
        return "openai"
    
    async def chat(
        self,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用OpenAI API"""
        # 转换消息格式
        api_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # 调用API
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            return LLMResponse(
                content=response.choices[0].message.content,
                model=model,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
                finish_reason=response.choices[0].finish_reason,
            )
        except Exception as e:
            raise Exception(f"OpenAI API error: {str(e)}")
    
    async def health_check(self) -> bool:
        try:
            # 简单的健康检查：调用API
            await self.chat(
                messages=[Message(role="user", content="test")],
                model="gpt-3.5-turbo",
                max_tokens=10,
            )
            return True
        except:
            return False


class LLMClient:
    """统一的LLM客户端"""
    
    def __init__(self) -> None:
        self.providers: Dict[str, LLMProvider] = {}
    
    def register_provider(self, provider: LLMProvider) -> None:
        """注册provider"""
        self.providers[provider.name] = provider
    
    async def chat(
        self,
        provider_name: str,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用指定provider的chat接口"""
        provider = self.providers.get(provider_name)
        if not provider:
            raise ValueError(f"Provider {provider_name} not found")
        
        return await provider.chat(messages, model, temperature, max_tokens, **kwargs)
    
    async def health_check(self, provider_name: str) -> bool:
        """检查provider健康状态"""
        provider = self.providers.get(provider_name)
        if not provider:
            return False
        return await provider.health_check()


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI兼容的API Provider（支持newapi、bbapi、hejuapi等）"""
    
    def __init__(self, name: str, api_key: str, base_url: str) -> None:
        self._name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=60.0, proxy=None)
    
    @property
    def name(self) -> str:
        return self._name
    
    async def chat(
        self,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用OpenAI兼容的API"""
        # 转换消息格式
        api_messages = []
        for msg in messages:
            msg_dict = {"role": msg.role, "content": msg.content}
            # 如果是tool消息，添加tool_call_id
            if msg.role == "tool" and msg.tool_call_id:
                msg_dict["tool_call_id"] = msg.tool_call_id
            api_messages.append(msg_dict)
        
        # 构建请求
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # 如果提供了tools参数，添加到payload
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        
        try:
            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            message = data["choices"][0]["message"]
            
            return LLMResponse(
                content=message.get("content") or "",
                model=model,
                usage=data.get("usage", {}),
                finish_reason=data["choices"][0].get("finish_reason"),
                tool_calls=message.get("tool_calls"),  # 可能为None
            )
        except Exception as e:
            # 打印详细错误信息用于调试
            error_msg = f"{self._name} API error: {str(e)}"
            try:
                if 'response' in locals():
                    error_msg += f"\n响应状态码: {response.status_code}"
                    error_msg += f"\n响应内容: {response.text[:500]}"
            except:
                pass
            raise Exception(error_msg)
    
    async def health_check(self) -> bool:
        try:
            await self.chat(
                messages=[Message(role="user", content="test")],
                model="gpt-3.5-turbo",
                max_tokens=10,
            )
            return True
        except:
            return False
    
    async def close(self):
        await self.client.aclose()
