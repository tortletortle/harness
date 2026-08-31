"""应用内置对话助手（chat 会话层）。

定位（用户定义）：EAS 应用内置的交互式助手——用户随时对话，助手理解意图后
可以直接调用工具干活（受 Grant 约束），也可以直接回答。不是刚性流水线，
而是"有制度、有记忆、能干活"的常驻对话。

安全边界：
- 每次提问签发会话级 Grant（工具白名单 + 调用预算 + 时效），与计划执行的
  Grant 同一套授权体系；
- 默认工具集 = 全部确定性工具中排除 external 类（对外发送不可回退），
  config chat.tools / chat.exclude_tools 可覆盖；
- 工具调用的产出与拒绝都进审计与消息历史，用户可见。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from authorization import GrantLimits
from capabilities import CapabilityRegistry, CapabilityRequest
from llm_client import LLMClient, Message
from logging_config import get_logger

logger = get_logger(__name__)


class ChatSession:
    """一个对话会话：消息历史 + 工具调用循环 + 会话级 Grant。"""

    def __init__(
        self,
        session_id: str,
        llm_client: LLMClient,
        capability_registry: CapabilityRegistry,
        config: Dict[str, Any],
        authorization_manager: Any = None,
    ) -> None:
        self.session_id = session_id
        self.llm_client = llm_client
        self.capability_registry = capability_registry
        self.authorization_manager = authorization_manager
        self.config = config or {}
        self.created_at = time.time()
        self.messages: List[Message] = []

        chat_cfg = self.config
        self.provider = str(chat_cfg.get("provider") or "")
        self.model = str(chat_cfg.get("model") or "")
        self.temperature = float(chat_cfg.get("temperature", 0.3))
        self.max_tool_rounds = max(1, int(chat_cfg.get("max_tool_rounds", 6)))
        self.max_tool_calls = max(1, int(chat_cfg.get("max_tool_calls", 20)))
        self.exclude = set(chat_cfg.get("exclude_tools") or [])

        # 工具白名单：显式 tools 配置优先；缺省 = 全部确定性工具中排除
        # external 类与 manual_tool（人工步骤不进对话）。
        explicit = chat_cfg.get("tools")
        if isinstance(explicit, list) and explicit:
            self.allowed_tools = [str(t) for t in explicit]
        else:
            self.allowed_tools = [
                spec.id
                for spec in capability_registry.list_specs()
                if spec.kind == "deterministic_tool" and spec.id not in ("manual_tool",)
                and spec.id not in self.exclude
                and spec.side_effect != "external"
            ]

        # 会话级工具定义（OpenAI function 格式）
        self.tool_defs = self._build_tool_defs()

    # ---- 工具定义 ----

    def _build_tool_defs(self) -> List[Dict[str, Any]]:
        defs: List[Dict[str, Any]] = []
        for spec in self.capability_registry.list_specs():
            if spec.kind != "deterministic_tool" or spec.id not in self.allowed_tools:
                continue
            parameters = spec.input_schema if isinstance(spec.input_schema, dict) and spec.input_schema.get("type") else {
                "type": "object",
                "properties": {},
            }
            defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.id,
                        "description": (spec.description or spec.id)[:300],
                        "parameters": parameters,
                    },
                }
            )
        return defs

    # ---- 授权 ----

    def _issue_grant(self) -> str:
        grant = self.authorization_manager.issue_grant(
            subject=f"chat:{self.session_id}",
            allowed_tools=self.allowed_tools,
            limits=GrantLimits(
                max_tool_calls=self.max_tool_calls,
                max_model_calls=self.max_tool_rounds + 1,
            ),
            ttl_seconds=600,
        )
        return grant.grant_id

    # ---- 主流程 ----

    def _build_system_prompt(self) -> str:
        from tools.project_memory import recall as _memory_recall

        try:
            mem = _memory_recall(2000)
            memory_text = mem.get("content", "") if mem.get("success") else ""
        except Exception:
            memory_text = ""
        parts = [
            "你是 EAS 应用的内置助手：帮助用户完成开发任务，可以直接调用工具（受授权约束）。",
            "回答要简洁、可执行；不确定时先问。",
            "如果任务复杂，建议用户走正式流程（提交需求 → 设计书 → 审批 → 计划执行）。",
        ]
        if memory_text and not memory_text.startswith("（暂无"):
            parts.append("项目工作记忆（最近状态）：\n" + memory_text)
        return "\n".join(parts)

    async def ask(self, user_text: str) -> Dict[str, Any]:
        """处理一轮用户输入，返回 {reply, tool_calls_made, session_id}。"""
        if not any(m.role == "system" for m in self.messages):
            self.messages.insert(
                0, Message(role="system", content=self._build_system_prompt())
            )
        self.messages.append(Message(role="user", content=str(user_text)))
        grant_id = self._issue_grant()
        tool_calls_made: List[Dict[str, Any]] = []
        reply = ""

        try:
            for _turn in range(self.max_tool_rounds):
                response = await self.llm_client.chat(
                    provider_name=self.provider,
                    messages=self.messages,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=4000,
                    tools=self.tool_defs or None,
                )
                self.messages.append(
                    Message(role="assistant", content=response.content or "")
                )

                if not response.tool_calls:
                    reply = response.content or ""
                    break

                # 执行工具调用（每个 response 最多取 3 个，防止失控）
                for tc in response.tool_calls[:3]:
                    fn = (tc.get("function") or {})
                    name = str(fn.get("name") or "").strip()
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except Exception:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}

                    cap = self.capability_registry.get(name)
                    if cap is None or name not in self.allowed_tools:
                        tool_result: Dict[str, Any] = {
                            "success": False,
                            "error": f"工具不可用: {name}",
                        }
                    else:
                        decision = self.authorization_manager.authorize(
                            grant_id, name, "execute", args
                        )
                        if not decision.allowed:
                            tool_result = {"success": False, "error": decision.message}
                        else:
                            try:
                                cap_result = await self.capability_registry.run(
                                    name,
                                    CapabilityRequest(
                                        work_item_id=f"chat:{self.session_id}",
                                        goal="chat tool call",
                                        inputs=args,
                                        grant_id=grant_id,
                                    ),
                                    {"authorization_manager": self.authorization_manager, "grant_id": grant_id},
                                )
                                tool_result = {
                                    "success": cap_result.status == "completed",
                                    "output": cap_result.output,
                                    "error": cap_result.error,
                                }
                            except Exception as exc:
                                tool_result = {"success": False, "error": f"工具执行异常: {exc}"}

                    tool_calls_made.append({"tool": name, "args": args, "result": tool_result})
                    self.messages.append(
                        Message(
                            role="tool",
                            content=json.dumps(tool_result, ensure_ascii=False)[:4000],
                            tool_call_id=tc.get("id"),
                        )
                    )
            else:
                reply = "（本轮工具调用轮次已达上限，请继续提问）"
        finally:
            pass

        return {
            "reply": reply or "（无回复）",
            "tool_calls_made": tool_calls_made,
            "session_id": self.session_id,
        }


class ChatManager:
    """会话管理器：按 session_id 创建/复用会话。"""

    def __init__(
        self,
        llm_client: LLMClient,
        capability_registry: CapabilityRegistry,
        authorization_manager: Any,
        config: Dict[str, Any],
    ) -> None:
        self.llm_client = llm_client
        self.capability_registry = capability_registry
        self.authorization_manager = authorization_manager
        self.config = dict(config or {})
        self.sessions: Dict[str, ChatSession] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> ChatSession:
        sid = session_id or f"chat-{uuid.uuid4().hex[:12]}"
        if sid not in self.sessions:
            self.sessions[sid] = ChatSession(
                sid,
                self.llm_client,
                self.capability_registry,
                self.config.get("chat", {}),
                authorization_manager=self.authorization_manager,
            )
        return self.sessions[sid]

    def remove(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None

    def stats(self) -> Dict[str, Any]:
        return {"sessions": len(self.sessions), "session_ids": list(self.sessions.keys())}


__all__ = ["ChatSession", "ChatManager"]

# GrantLimits 延迟引用说明：authorization_manager.issue_grant 在运行时使用，
# 此处不导入以避免循环依赖。
