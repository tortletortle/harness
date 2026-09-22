"""
LLM客户端 - 统一的LLM调用接口

支持多个provider（newapi, bbapi, hejuapi, stepfun等），插件化设计
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
import httpx
import json


class LLMRequestError(RuntimeError):
    """Structured upstream error; status/retry semantics survive generation wrapping."""
    def __init__(self, message, *, status_code=None, retry_after=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable


def _retry_after(response):
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    raw = getattr(response, "headers", {}).get("retry-after")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


# A threading semaphore is deliberately used: providers also run in worker event
# loops. No asyncio primitive/client is shared across loops, and cancellation never
# leaves an acquire blocked in a worker thread. Scope is this process, not a quota
# shared by unrelated applications/processes using the same API key.
import threading as _threading
_provider_slots = {}
_provider_slots_lock = _threading.Lock()


def _provider_slot(base_url, api_key):
    import hashlib
    import os
    key = (base_url, hashlib.sha256(api_key.encode()).hexdigest())
    with _provider_slots_lock:
        if key not in _provider_slots:
            limit = max(1, int(os.getenv("EAS_LLM_MAX_CONCURRENCY", "2")))
            _provider_slots[key] = _threading.BoundedSemaphore(limit)
        return _provider_slots[key]


async def _acquire_provider_slot(slot):
    import asyncio
    import time
    deadline = time.monotonic() + 180
    while not slot.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise LLMRequestError("本进程模型并发队列等待超时", status_code=429,
                                  retry_after=1, retryable=True)
        await asyncio.sleep(0.05)


class Message(BaseModel):
    """消息"""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_call_id: Optional[str] = None  # 用于tool角色的消息
    tool_calls: Optional[List[Dict[str, Any]]] = None  # assistant 消息携带的函数调用请求（多轮工具调用）


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


class DeterministicProvider(LLMProvider):
    """离线测试用 provider，返回固定响应且不访问网络。"""

    def __init__(self, response: str, provider_name: str = "deterministic") -> None:
        self.response = response
        self._name = provider_name
        self.calls: list[list[Message]] = []

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
        self.calls.append(messages)
        return LLMResponse(content=self.response, model=model, finish_reason="stop")

    async def health_check(self) -> bool:
        return True


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


try:
    from utils.usage_tracker import record_usage
except Exception:  # pragma: no cover - 用量模块缺失不应影响 LLM 调用
    record_usage = None


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

        response = await provider.chat(messages, model, temperature, max_tokens, **kwargs)

        # P0 用量记账：所有 provider 调用统一在此记录；失败绝不影响主链路
        if record_usage is not None:
            try:
                record_usage(provider_name, model, getattr(response, "usage", None))
            except Exception:
                pass
        return response
    
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
        self._slot = _provider_slot(self.base_url, api_key)

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
            # assistant 消息携带的函数调用请求（多轮工具调用回传）
            if msg.role == "assistant" and msg.tool_calls:
                msg_dict["tool_calls"] = msg.tool_calls
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
            # 每次调用现开现关：httpx 连接池会绑定首次使用的事件循环，
            # 复用长命 client 在跨 asyncio.run()/多循环（脚本、worker 线程）场景
            # 会报 "Event loop is closed"。LLM 调用以秒计，建连开销可忽略。
            await _acquire_provider_slot(self._slot)
            try:
                async with httpx.AsyncClient(timeout=180.0, proxy=None, trust_env=False) as client:
                    response = await client.post(url, json=payload, headers=headers)
            finally:
                self._slot.release()
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
            error_msg = f"{self._name} API error: [{type(e).__name__}] {str(e) or chr(60) + chr(101) + chr(109) + chr(112) + chr(116) + chr(121) + chr(32) + chr(109) + chr(101) + chr(115) + chr(115) + chr(97) + chr(103) + chr(101) + chr(62)}"
            try:
                if 'response' in locals():
                    error_msg += f"\n响应状态码: {response.status_code}"
                    error_msg += f"\n响应内容: {response.text[:500]}"
            except:
                pass
            if isinstance(e, LLMRequestError):
                raise
            error_msg = error_msg.replace(self.api_key, "[REDACTED]") if self.api_key else error_msg
            status = getattr(locals().get("response"), "status_code", None)
            raise LLMRequestError(
                error_msg, status_code=status, retry_after=_retry_after(locals().get("response")),
                retryable=status in {408, 429, 500, 502, 503, 504} or isinstance(e, httpx.TransportError),
            ) from e
    
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
        # client 已改为 per-call 创建，保留方法以兼容旧调用方
        return None
