"""CodingAgentTool —— Grant 受限的代码修改 Agent（v2：有界多轮）。

最小闭环（设计稿 §19"Agent 即工具"）+ 多轮迭代（§22.7 AgentSession 第一阶段）：

    WorkGraph 步骤 (tool=coding_agent)
      → Runtime 签发 Grant（内部工具 text_editor[/test_runner] + 目标文件资源，
        预算按 inputs.max_turns 扩展，见 authorization.agent_step_grant_params）
      → 每轮：受权读取目标文件 → 单次模型调用（结构化 JSON，完整文件内容）
        → 受权写回（写入位置固定为步骤声明的 path，模型无法重定向）
      → 多轮时：写回后可用 test_runner 自测（Grant 声明了才启用），
        测试输出作为结构化反馈进入下一轮提示词；通过即提前结束
      → 返回结构化 CapabilityResult，最终验收仍归 AcceptanceRunner /
        后续 test_runner 步骤（agent 不自我宣布成功）

fail-closed 边界（与 1.3 一致）：
- 无 Grant / 过期 / 撤销 / 超预算 → 失败；模型输出 path 不一致 → 不写入判失败；
- 内部工具未授权 → 拒绝执行；非法 JSON → 安全失败。
- max_turns 缺省 = 1：行为与单轮版完全一致（向后兼容）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from agent_tools import AgentRuntimeContext, BaseAgentTool, _parse_result
from authorization import AuthorizationManager
from capabilities import CapabilityRequest, CapabilityResult, CapabilitySpec
from grant_llm_client import GrantLLMClient
from llm_client import Message

# 提供给模型的文件现状上限（字符），防止超大文件撑爆上下文。
_MAX_FILE_CONTEXT_CHARS = 12000
# 反馈里测试输出保留的字符数。
_MAX_FEEDBACK_CHARS = 1500


class CodingAgentTool(BaseAgentTool):
    """有界多轮 coding agent：模型提议内容，Runtime 决定写哪里、能不能写、能改几轮。"""

    # 声明的内部工具：通过 spec 暴露给 Runtime，由 Grant 签发方并入 allowed_tools。
    inner_tools = ("text_editor",)

    def __init__(
        self,
        llm_client: Any,
        authorization_manager: AuthorizationManager,
        tool_registry: Any,
        provider_name: str,
        model: str,
        agent_id: str = "coding_agent",
        description: str = "读取指定文件并按目标生成修改后的完整文件内容",
        database: Any = None,
    ) -> None:
        self.agent_id = agent_id
        self.description = description
        self.llm_client = llm_client
        self.authorization_manager = authorization_manager
        self.tool_registry = tool_registry
        self.provider_name = provider_name
        self.model = model
        # 可选：内部工具调用的审计落库（与外层步骤审计共享 audit_logs 表）。
        self.database = database

    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            id=self.agent_id,
            version="0.2.0",
            kind="agent_tool",
            required_permissions=(f"agent:{self.agent_id}",),
            side_effect="reversible",
            idempotent=False,
            budget_spec={
                "max_model_calls": 1,  # 单轮缺省；多轮由 Runtime 按 inputs.max_turns 扩展
                "inner_tools": list(self.inner_tools),
            },
            can_spawn_agents=False,
            description=self.description,
        )

    async def run(self, request: CapabilityRequest, context: AgentRuntimeContext) -> CapabilityResult:
        error = self._validate_request(request, context)
        if error:
            return CapabilityResult(status="failed", error=error)

        inputs = request.inputs or {}
        target_path = str(inputs.get("path") or "").strip()
        if not target_path:
            return CapabilityResult(
                status="failed",
                error="coding_agent 需要输入 path（Grant 资源声明的目标文件）",
            )
        try:
            max_turns = max(1, int(inputs.get("max_turns", 1)))
        except (TypeError, ValueError):
            max_turns = 1

        facts: List[Dict[str, Any]] = [
            {"key": "coding_agent_target", "value": target_path},
        ]

        # P2 反馈回路：执行器的验收缺口通过 inputs.feedback 注入首轮提示词
        feedback = str(inputs.get("feedback") or "")
        last_test_exit: Optional[int] = None
        turns_used = 0

        # P2 上下文编排：附仓库地图（Aider 模式，fail-open；静默降级不写日志）
        repo_map_block = ""
        try:
            import asyncio as _asyncio
            from knowledge.injection import build_repo_map_block
            repo_map_block = await _asyncio.to_thread(build_repo_map_block, 2500) or ""
        except Exception:
            repo_map_block = ""

        # 对话式消息序列：首轮之后不再重建完整提示词，而是追加 assistant/user 消息对。
        # 前缀（system + 首轮请求 + 各轮 assistant 输出）跨轮保持不变，
        # 使上游前缀缓存（KV cache）可以命中，降低多轮时的延迟与费用。
        messages: Optional[List[Message]] = None

        for turn in range(1, max_turns + 1):
            # ---- 每轮第一步：受权读取现状 ----
            read_status, read_result, read_error = await self._inner_tool(
                context, request, "text_editor", "read_file", {"path": target_path}
            )
            if read_status == "denied":
                return CapabilityResult(
                    status="failed",
                    error=f"coding_agent 读取被拒绝: {read_error}",
                    facts=facts,
                )
            if read_status == "error" and "File not found" not in str(read_error):
                return CapabilityResult(
                    status="failed",
                    error=f"coding_agent 读取失败: {read_error}",
                    facts=facts,
                )
            current_content = (read_result or {}).get("content", "") if read_status == "ok" else ""
            if turn == 1:
                facts.append(
                    {"key": "coding_agent_file_exists", "value": "true" if read_status == "ok" else "false"}
                )

            # ---- 模型调用（GrantLLMClient 在调用前强制预算）----
            if messages is None:
                # 首轮：完整初始提示词（此后作为稳定前缀复用，提高上游缓存命中）
                prompt = self._build_prompt(request, target_path, current_content, feedback, repo_map_block)
                messages = [
                    Message(role="system", content=context.system_prompt or self.description),
                    Message(role="user", content=prompt),
                ]
            else:
                # 后续轮：追加上一轮 assistant 输出与本轮反馈（测试输出 + 当前文件回显）
                messages.append(Message(role="assistant", content=response_content))
                messages.append(Message(role="user", content=self._build_followup(feedback, current_content)))
            client = GrantLLMClient(
                context.llm_client,
                context.authorization_manager,
                context.grant_id,
            )
            try:
                response = await client.chat(
                    provider_name=context.provider_name,
                    messages=messages,
                    model=context.model,
                    temperature=0.0,
                    max_tokens=4000,
                )
                response_content = response.content
            except PermissionError as exc:
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(status="failed", error=str(exc), facts=facts)
            except Exception as exc:
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"coding_agent 模型调用失败: {exc}",
                    facts=facts,
                )

            parsed = _parse_result(response.content)
            if parsed is None:
                # 修复尝试：尾逗号 / 字符串内裸换行等已知噪声（json_repair，保守策略）
                from json_repair import parse_loose
                repaired = parse_loose(response.content)
                if isinstance(repaired, dict):
                    parsed = repaired
                    facts.append({"key": "coding_agent_json_repaired", "value": f"turn{turn}"})
            if parsed is None:
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"第 {turn} 轮返回不是合法 JSON",
                    facts=facts,
                )

            output = parsed.get("output")
            if isinstance(output, dict):
                proposed_path = output.get("path")
                new_content = output.get("content")
            elif isinstance(output, str):
                proposed_path = None
                new_content = output
            else:
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"第 {turn} 轮输出缺少 content 字段",
                    facts=facts,
                )
            if not isinstance(new_content, str):
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"第 {turn} 轮的 content 不是字符串",
                    facts=facts,
                )

            # 写入位置由 Runtime 决定；模型声明的 path 只能与目标一致。
            if proposed_path and str(proposed_path).replace("\\", "/") != target_path.replace("\\", "/"):
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"模型试图写入非授权路径: {proposed_path}（目标: {target_path}）",
                    facts=facts,
                )

            # ---- 空内容防护：向已有文件写入空内容是破坏性操作 ----
            if isinstance(new_content, str) and not new_content.strip():
                if turn < max_turns:
                    response_content = response.content
                    feedback = (
                        f"第 {turn} 轮输出的 content 为空。向目标文件写入空内容是破坏性操作、已被拒绝。"
                        "请重新输出目标文件的完整非空内容。"
                    )
                    messages.append(Message(role="assistant", content=response_content))
                    messages.append(Message(role="user", content=feedback))
                    continue
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"第 {turn} 轮输出为空内容，已拒绝写入（文件保持原状）",
                    facts=facts,
                )

            # ---- 受权写回 ----
            write_status, _, write_error = await self._inner_tool(
                context, request, "text_editor", "write_file",
                {"path": target_path, "content": new_content},
            )
            if write_status != "ok":
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"coding_agent 写入被拒绝: {write_error}",
                    facts=facts,
                )
            turns_used = turn
            facts.append({"key": f"coding_agent_turn{turn}_bytes", "value": len(new_content.encode("utf-8"))})

            # ---- 多轮自测：Grant 声明了 test_runner 且提供了测试参数才启用 ----
            # 注意：测试失败（exit_code 非零）是合法反馈而非工具错误。
            if "test_runner" not in (request.allowed_tools or []):
                break
            suite = str(inputs.get("test_suite") or "").strip()
            test_path = str(inputs.get("test_path") or "").strip()
            if not suite:
                break
            tr_status, tr_payload, tr_extra = await self._inner_tool(
                context, request, "test_runner", "run",
                {"suite": suite, "test_path": test_path},
            )
            tr_result = tr_payload if tr_status == "ok" else (tr_extra or {})
            if not isinstance(tr_result, dict) or tr_result.get("exit_code") is None:
                # 被拒绝（denied）或基础设施失败（命令不存在等）→ 无有效反馈，
                # 停止自测循环，交给外层验收。
                facts.append({"key": "coding_agent_selftest", "value": f"skipped: {tr_status}"})
                break
            last_test_exit = tr_result.get("exit_code")
            if last_test_exit == 0:
                facts.append({"key": "coding_agent_selftest", "value": "passed"})
                break
            feedback = (
                f"第 {turn} 轮修改后自测未通过（exit_code={last_test_exit}）。\n"
                f"测试输出摘要：\n{_tail(tr_result.get('stderr') or tr_result.get('stdout') or '', _MAX_FEEDBACK_CHARS)}\n"
                "请修复问题后重新输出完整文件内容。"
            )
            facts.append({"key": f"coding_agent_turn{turn}_test_exit", "value": last_test_exit})

        grant = context.authorization_manager.get(context.grant_id)
        facts.append({"key": "coding_agent_turns_used", "value": turns_used})
        if last_test_exit is not None:
            facts.append({"key": "coding_agent_last_test_exit", "value": last_test_exit})
        return CapabilityResult(
            status="completed",
            output={"path": target_path, "turns": turns_used},
            artifacts=[f"file://{target_path}"],
            facts=facts,
            usage=grant.usage() if grant else {},
            acceptance_evidence=[],
        )

    async def _inner_tool(
        self,
        context: AgentRuntimeContext,
        request: CapabilityRequest,
        tool_id: str,
        instruction: str,
        inputs: Dict[str, Any],
    ) -> Tuple[str, Optional[Dict[str, Any]], Any]:
        """内部工具调用：先 authorize（消耗 Grant 次数），再同步执行并写审计。

        返回 (status, result, extra)；status ∈ {"ok", "denied", "error"}，
        result 为工具完整结果字典（ok 时），extra 为拒绝原因或失败结果。
        注意：对 test_runner，"测试失败（exit_code 非零）"是 ok 结果——
        工具成功执行了测试；真正的反馈判断由调用方按 exit_code 处理。
        """
        decision = context.authorization_manager.authorize(
            context.grant_id, tool_id, instruction, inputs
        )
        if not decision.allowed:
            return "denied", None, {"reason": decision.reason, "message": decision.message}
        result = await asyncio.to_thread(
            self.tool_registry.execute_tool, tool_id, instruction, inputs
        )
        self._audit(context, request, tool_id, instruction, inputs, result)
        if result.get("success"):
            return "ok", result, None
        return "error", None, result

    def _audit(
        self,
        context: AgentRuntimeContext,
        request: CapabilityRequest,
        tool_id: str,
        instruction: str,
        inputs: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        if self.database is None:
            return
        try:
            self.database.save_audit_log(
                tool_id=tool_id,
                instruction=f"{self.agent_id}:{instruction}",
                inputs=inputs,
                step_id=str(request.work_item_id) if request.work_item_id else None,
                success=bool(result.get("success")),
                blocked=False,
                block_reason=None,
                actor=f"agent:{self.agent_id}",
                grant_id=context.grant_id,
            )
        except Exception:
            # 审计失败不影响执行主流程（与 executor._audit 同一口径）。
            pass

    @staticmethod
    def _build_prompt(
        request: CapabilityRequest,
        target_path: str,
        current_content: str,
        feedback: str = "",
        repo_map_block: str = "",
    ) -> str:
        inputs = request.inputs or {}
        instruction = str(inputs.get("instruction") or request.goal)
        if current_content:
            if len(current_content) > _MAX_FILE_CONTEXT_CHARS:
                current_content = current_content[:_MAX_FILE_CONTEXT_CHARS] + "\n...[内容已截断]"
            current_block = f"文件当前内容：\n```\n{current_content}\n```"
        else:
            current_block = "文件当前不存在，需要新建。"
        feedback_block = f"\n上一轮反馈：\n{feedback}\n" if feedback else ""
        map_block = f"\n{repo_map_block}\n" if repo_map_block else ""
        return (
            "只完成当前 WorkItem，不创建子 Agent，不扩大权限，不读写目标以外的文件。\n"
            f"目标：{request.goal}\n"
            f"修改指令：{instruction}\n"
            f"目标文件：{target_path}\n"
            f"{current_block}"
            f"{map_block}"
            f"{feedback_block}\n"
            "只输出 JSON（不要输出其他文字）：\n"
            '{"status":"completed|needs_input|failed",'
            '"output":{"path":"' + target_path + '","content":"修改后的完整文件内容"},'
            '"artifacts":[],"facts":[],"error":null}\n'
            "output.content 必须是修改后的完整文件内容（不是 diff、不是片段）；"
            "output.path 必须与目标文件一致。"
        )


    @staticmethod
    def _build_followup(feedback: str, current_content: str) -> str:
        """后续轮的 user 消息：测试反馈 + 磁盘上的当前文件回显。

        只追加、不重建历史消息，使前缀（system + 首轮请求 + 各轮 assistant）
        跨轮保持不变，提高上游前缀缓存的命中率。
        """
        if len(current_content) > _MAX_FILE_CONTEXT_CHARS:
            current_content = current_content[:_MAX_FILE_CONTEXT_CHARS] + "\n...[内容已截断]"
        return (
            "上一轮反馈：\n"
            f"{feedback}\n"
            "文件当前内容（以磁盘为准）：\n"
            f"```\n{current_content}\n```\n"
            "请再次只输出 JSON（结构同前），output.content 为修改后的完整文件内容。"
        )


def _tail(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else "...[截断]\n" + text[-limit:]


__all__ = ["CodingAgentTool"]
