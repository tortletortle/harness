"""LLM client wrapper that enforces a Runtime Grant before each model call."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from authorization import AuthorizationManager
from llm_client import LLMClient, LLMResponse, Message


class GrantLLMClient:
    """Explicitly grant-bound facade over the shared LLMClient.

    The grant id is passed by Runtime at the call site; it is never inferred
    from model messages and never inserted into the prompt.
    """

    def __init__(
        self,
        client: LLMClient,
        authorization_manager: AuthorizationManager,
        grant_id: str,
    ) -> None:
        self.client = client
        self.authorization_manager = authorization_manager
        self.grant_id = grant_id

    async def chat(
        self,
        provider_name: str,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> LLMResponse:
        decision = self.authorization_manager.record_model_call(self.grant_id)
        if not decision.allowed:
            raise PermissionError(f"{decision.reason}: {decision.message}")

        response = await self.client.chat(
            provider_name=provider_name,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        usage = response.usage if isinstance(response.usage, dict) else {}
        usage_decision = self.authorization_manager.record_model_usage(self.grant_id, usage)
        if not usage_decision.allowed:
            raise RuntimeError(f"无法记录模型 usage: {usage_decision.message}")
        return response


__all__ = ["GrantLLMClient"]
