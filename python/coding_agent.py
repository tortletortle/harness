"""CodingAgentTool —— Grant 受限的单轮代码修改 Agent 原型。

最小闭环（设计稿 §19"Agent 即工具"第二阶段第一步）：

    WorkGraph 步骤 (tool=coding_agent)
      → Runtime 签发 Grant（内部工具 text_editor + 目标文件资源，见
        authorization.agent_step_grant_params）
      → 受权读取目标文件（text_editor:read_file，authorize + 审计）
      → 单次模型调用：只输出结构化 JSON（修改后的完整文件内容）
      → 写入位置固定为步骤输入声明的 path（模型无法重定向）
      → 受权写回（text_editor:write_file，authorize + 审计）
      → 返回结构化 CapabilityResult，验收交给 AcceptanceRunner

fail-closed 边界：
- 无 Grant / Grant 过期 / 撤销 / 超预算 → 直接失败。
- 模型输出 path 与目标不一致 → 不写入，判失败（模型不能重定向写位置）。
- 内部工具未获 Grant 授权（tool_not_granted / budget_exceeded）→ 不执行，判失败。
- 模型输出不是合法 JSON 或缺少 content → 安全失败，不伪装成功。

本模块有意保持单轮：这里是契约/协议层，不是另一个递归 Agent 编排器。
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


class CodingAgentTool(BaseAgentTool):
    """单轮 coding agent：模型提议内容，Runtime 决定写哪里、能不能写。"""

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
            version="0.1.0",
            kind="agent_tool",
            required_permissions=(f"agent:{self.agent_id}",),
            side_effect="reversible",
            idempotent=False,
            budget_spec={
                "max_model_calls": 1,
                "inner_tools": list(self.inner_tools),
            },
            can_spawn_agents=False,
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

        # 1) 受权读取现状。
        #    - 授权拒绝（denied）→ 立即失败：这是授权边界的直接信号；
        #    - 文件不存在（File not found）→ 新建场景，空内容继续；
        #    - 其他读取错误 → 失败：在未知现状时盲写整个文件不安全。
        read_status, current_content, read_error = await self._inner_tool(
            context, request, "read_file", {"path": target_path}
        )
        if read_status == "denied":
            return CapabilityResult(
                status="failed",
                error=f"coding_agent 读取被拒绝: {read_error}",
            )
        if read_status == "error" and "File not found" not in str(read_error):
            return CapabilityResult(
                status="failed",
                error=f"coding_agent 读取失败: {read_error}",
            )
        facts: List[Dict[str, Any]] = [
            {"key": "coding_agent_target", "value": target_path},
            {"key": "coding_agent_file_exists", "value": "true" if read_status == "ok" else "false"},
        ]

        # 2) 单次模型调用（GrantLLMClient 在调用前强制预算）。
        prompt = self._build_prompt(request, target_path, current_content)
        client = GrantLLMClient(
            context.llm_client,
            context.authorization_manager,
            context.grant_id,
        )
        try:
            response = await client.chat(
                provider_name=context.provider_name,
                messages=[
                    Message(role="system", content=context.system_prompt or self.description),
                    Message(role="user", content=prompt),
                ],
                model=context.model,
                temperature=0.0,
                max_tokens=4000,
            )
        except PermissionError as exc:
            return CapabilityResult(status="failed", error=str(exc), facts=facts)
        except Exception as exc:
            return CapabilityResult(
                status="failed",
                error=f"coding_agent 模型调用失败: {exc}",
                facts=facts,
            )

        parsed = _parse_result(response.content)
        if parsed is None:
            # 模型输出无法解析时安全失败，不把自然语言伪装成完成。
            return CapabilityResult(
                status="failed",
                error="coding_agent 返回不是合法 JSON",
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
            return CapabilityResult(
                status="failed",
                error="coding_agent 输出缺少 content 字段",
                facts=facts,
            )
        if not isinstance(new_content, str):
            return CapabilityResult(
                status="failed",
                error="coding_agent 的 content 不是字符串",
                facts=facts,
            )

        # 3) 写入位置由 Runtime 决定；模型声明的 path 只能与目标一致，不能重定向。
        if proposed_path and str(proposed_path).replace("\\", "/") != target_path.replace("\\", "/"):
            return CapabilityResult(
                status="failed",
                error=f"模型试图写入非授权路径: {proposed_path}（目标: {target_path}）",
                facts=facts,
            )

        # 4) 受权写回。写入被拒绝时文件保持原状（先拒绝后执行，无部分写入）。
        write_status, _, write_error = await self._inner_tool(
            context, request, "write_file", {"path": target_path, "content": new_content}
        )
        if write_status != "ok":
            return CapabilityResult(
                status="failed",
                error=f"coding_agent 写入被拒绝: {write_error}",
                facts=facts,
            )

        grant = context.authorization_manager.get(context.grant_id)
        size = len(new_content.encode("utf-8"))
        facts.append({"key": "coding_agent_bytes_written", "value": size})
        return CapabilityResult(
            status="completed",
            output={"path": target_path, "bytes": size, "content": new_content},
            artifacts=[f"file://{target_path}"],
            facts=facts,
            usage=grant.usage() if grant else {},
            acceptance_evidence=[],
        )

    async def _inner_tool(
        self,
        context: AgentRuntimeContext,
        request: CapabilityRequest,
        instruction: str,
        inputs: Dict[str, Any],
    ) -> Tuple[str, Any, Optional[str]]:
        """内部工具调用：先 authorize（消耗 Grant 次数），再同步执行并写审计。

        返回 (status, content, error)；status ∈ {"ok", "denied", "error"}，
        denied 表示 Grant 授权拒绝（结构化原因在 error），error 表示工具执行失败。
        """
        decision = context.authorization_manager.authorize(
            context.grant_id, "text_editor", instruction, inputs
        )
        if not decision.allowed:
            return "denied", None, f"{decision.reason}: {decision.message}"
        result = await asyncio.to_thread(
            self.tool_registry.execute_tool, "text_editor", instruction, inputs
        )
        self._audit(context, request, instruction, inputs, result)
        if result.get("success"):
            return "ok", result.get("content", ""), None
        return "error", None, str(result.get("error", "text_editor 调用失败"))

    def _audit(
        self,
        context: AgentRuntimeContext,
        request: CapabilityRequest,
        instruction: str,
        inputs: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        if self.database is None:
            return
        try:
            self.database.save_audit_log(
                tool_id="text_editor",
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
    def _build_prompt(request: CapabilityRequest, target_path: str, current_content: str) -> str:
        inputs = request.inputs or {}
        instruction = str(inputs.get("instruction") or request.goal)
        if current_content:
            if len(current_content) > _MAX_FILE_CONTEXT_CHARS:
                current_content = current_content[:_MAX_FILE_CONTEXT_CHARS] + "\n...[内容已截断]"
            current_block = f"文件当前内容：\n```\n{current_content}\n```"
        else:
            current_block = "文件当前不存在，需要新建。"
        return (
            "只完成当前 WorkItem，不创建子 Agent，不扩大权限，不读写目标以外的文件。\n"
            f"目标：{request.goal}\n"
            f"修改指令：{instruction}\n"
            f"目标文件：{target_path}\n"
            f"{current_block}\n\n"
            "只输出 JSON（不要输出其他文字）：\n"
            '{"status":"completed|needs_input|failed",'
            '"output":{"path":"' + target_path + '","content":"修改后的完整文件内容"},'
            '"artifacts":[],"facts":[],"error":null}\n'
            "output.content 必须是修改后的完整文件内容（不是 diff、不是片段）；"
            "output.path 必须与目标文件一致。"
        )


__all__ = ["CodingAgentTool"]
