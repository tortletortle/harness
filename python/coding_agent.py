"""CodingAgentTool —— Grant 受限的代码修改 Agent（v2：有界多轮）。

最小闭环（设计稿 §19"Agent 即工具"）+ 多轮迭代（§22.7 AgentSession 第一阶段）：

    WorkGraph 步骤 (tool=coding_agent)
      → Runtime 签发 Grant（内部工具 text_editor[/test_runner] + 目标文件资源，
        预算按 inputs.max_turns 扩展，见 authorization.agent_step_grant_params）
      → 每轮：受权读取目标文件 → 单次模型调用
        （full 模式：结构化 JSON、完整文件内容；patch 模式：search/replace 补丁块，
          代码内容不进 JSON 字符串——削接缝动作 A，配合 python/patcher.py）
        → 受权写回（写入位置固定为步骤声明的 path，模型无法重定向）
      → 多轮时：写回后可用 test_runner 自测（Grant 声明了才启用），
        测试输出作为结构化反馈进入下一轮提示词；通过即提前结束
      → 返回结构化 CapabilityResult，最终验收仍归 AcceptanceRunner /
        后续 test_runner 步骤（agent 不自我宣布成功）

fail-closed 边界（与 1.3 一致）：
- 无 Grant / 过期 / 撤销 / 超预算 → 失败；模型输出 path 不一致 → 不写入判失败；
- 内部工具未授权 → 拒绝执行；非法 JSON → 安全失败。
- max_turns 缺省 = 1：行为与单轮版完全一致（向后兼容）。
 Turn 级压缩（v0.3）：每轮 chat 前估算输入 tokens，超预算（inputs.max_input_tokens /
 EAS_CODING_AGENT_MAX_INPUT_TOKENS / 缺省 48000）即折叠中间轮为确定性摘要行并写会话
 笔记（data/knowledge/notes/coding-agent-*.md，fail-open）；首轮超预算则动态收紧文件
 快照（指令本身过大才失败）。max_turns=1 且不超预算时消息与旧版逐字一致。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
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
# Turn 级压缩：输入 token 预算缺省值（inputs.max_input_tokens / 环境变量可覆盖）。
_DEFAULT_MAX_INPUT_TOKENS = 48000
_MAX_INPUT_TOKENS_ENV = "EAS_CODING_AGENT_MAX_INPUT_TOKENS"
# 压缩后保留的最近完整轮数（assistant+user 对）。
_COMPRESS_KEEP_LAST_TURNS = 1
# 首轮降级：预算余量比例（估算保守侧冗余）。
_FIRST_TURN_RESERVE_RATIO = 0.10
# 会话笔记：单文件上限（字符），超限停写。
_SESSION_NOTE_MAX_CHARS = 6000
# 折叠行标记前缀（压缩幂等：重建时替换旧折叠行而非嵌套）。
_FOLD_MARKER = "[历史压缩"


def _estimate_tokens(text: str) -> int:
    """启发式 token 估算（离线、确定性）：CJK 按 1 token/字，其余按 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk + 3) // 4


def _messages_tokens(messages: List[Message]) -> int:
    """消息序列的估算输入 tokens。"""
    return sum(_estimate_tokens(m.content or "") for m in messages)


def _summarize_turn_facts(facts: List[Dict[str, Any]], turn: int) -> str:
    """从 facts 还原第 turn 轮的执行摘要（压缩折叠行用；纯函数）。"""
    frags = []
    prefix = f"coding_agent_turn{turn}_"
    for fact in facts:
        key = fact.get("key") or ""
        if not key.startswith(prefix):
            continue
        kind = key[len(prefix):]
        value = fact.get("value")
        if kind == "bytes":
            frags.append(f"applied({value}B)")
        elif kind == "patch_error":
            frags.append(f"patch_error({value})")
        elif kind == "test_exit":
            frags.append(f"selftest_exit={value}")
        elif kind == "blocks_applied":
            frags.append(f"blocks={value}")
    return "; ".join(frags) or "done"


def _compress_messages(messages: List[Message], fold_text: str) -> List[Message]:
    """折叠中间轮：保留 system + 首轮 user + 折叠行 + 最近轮。

    旧折叠行（如有）位于 index 2，重建时自然丢弃，保证幂等不嵌套。
    """
    if len(messages) <= 2:
        return list(messages)
    keep = _COMPRESS_KEEP_LAST_TURNS
    head = messages[:2]
    tail = messages[-2 * keep:] if keep else []
    return head + [Message(role="user", content=fold_text)] + tail


def _session_note_filename(session_id: str, target_path: str) -> str:
    """会话笔记文件名（目标路径做 slug，防穿越）。"""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", target_path).strip("._")[:40]
    return f"coding-agent-{session_id[:8]}-{slug or 'note'}.md"


def _append_session_note(filename: str, header: str, entry: str) -> str:
    """向会话笔记追加一条压缩记录；超限返回 "capped"，否则返回落盘路径。

    IO 异常由调用方 fail-open 吞掉（观察性分层，不阻断主链路）。
    """
    from tools.workspace import resolve_workspace_root  # 延迟导入，避免循环依赖

    notes_dir = resolve_workspace_root() / "data" / "knowledge" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / filename
    if path.exists():
        current = path.read_text(encoding="utf-8", errors="replace")
        if len(current) + len(entry) + 1 > _SESSION_NOTE_MAX_CHARS:
            return "capped"
        path.write_text(current + entry + "\n", encoding="utf-8")
    else:
        path.write_text(header + "\n" + entry + "\n", encoding="utf-8")
    return str(path)


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
            version="0.3.0",
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

        # Turn 级压缩预算：步骤级 inputs 优先，其次环境变量，缺省 48000。
        try:
            max_input_tokens = int(
                inputs.get("max_input_tokens")
                or os.environ.get(_MAX_INPUT_TOKENS_ENV)
                or _DEFAULT_MAX_INPUT_TOKENS
            )
        except (TypeError, ValueError):
            max_input_tokens = _DEFAULT_MAX_INPUT_TOKENS
        max_input_tokens = max(1000, max_input_tokens)

        # 输出模式（动作 A）：步骤级 inputs.output_mode 优先，其次环境变量，缺省 full（向后兼容）。
        output_mode = str(
            inputs.get("output_mode") or os.environ.get("EAS_CODING_AGENT_OUTPUT_MODE") or "full"
        ).strip().lower()
        if output_mode not in ("patch", "full"):
            output_mode = "full"

        facts: List[Dict[str, Any]] = [
            {"key": "coding_agent_target", "value": target_path},
            {"key": "coding_agent_output_mode", "value": output_mode},
        ]

        # Session/Turn 记录（§8.1 分层）：一次 run = 一个 session，每轮模型调用 = 一个 turn。
        # 记录失败只降级（观察性分层），绝不影响执行主链路。
        session_id = str(uuid.uuid4())
        session_open = False
        if self.database is not None:
            try:
                self.database.create_agent_session(
                    session_id=session_id,
                    plan_id=getattr(context, "plan_id", "") or request.work_item_id,
                    step_id=str(request.work_item_id or ""),
                    agent_tool=self.agent_id,
                )
                session_open = True
                facts.append({"key": "agent_session_id", "value": session_id})
            except Exception:
                session_open = False

        def _turn_outcome(turn_row: Optional[int], outcome: str, detail: Optional[Dict[str, Any]] = None) -> None:
            if turn_row is None:
                return
            try:
                self.database.update_agent_turn_outcome(turn_row, outcome, detail)
            except Exception:
                pass

        def _finish_session(status: str, turns: int, error: Optional[str] = None) -> None:
            if not session_open:
                return
            try:
                grant_now = context.authorization_manager.get(context.grant_id)
                usage = grant_now.usage() if grant_now else {}
                self.database.finish_agent_session(
                    session_id=session_id, status=status, turns_used=turns,
                    model_calls=int(usage.get("model_calls") or 0),
                    tool_calls=int(usage.get("tool_calls") or 0),
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    error=error,
                )
            except Exception:
                pass

        # P2 反馈回路：执行器的验收缺口通过 inputs.feedback 注入首轮提示词
        feedback = str(inputs.get("feedback") or "")
        last_test_exit: Optional[int] = None
        turns_used = 0
        folded_through_turn = 0
        note_filename = _session_note_filename(session_id, target_path)
        note_header = (
            "# coding_agent 会话笔记\n"
            f"- session: {session_id}\n"
            f"- target: {target_path}\n"
            f"- goal: {str(request.goal or '')[:200]}\n"
            f"- date: {time.strftime('%Y-%m-%d %H:%M')}\n"
        )

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
                if output_mode == "patch":
                    prompt = self._build_prompt_patch(request, target_path, current_content, feedback, repo_map_block)
                else:
                    prompt = self._build_prompt(request, target_path, current_content, feedback, repo_map_block)
                messages = [
                    Message(role="system", content=context.system_prompt or self.description),
                    Message(role="user", content=prompt),
                ]
            else:
                # 后续轮：追加上一轮 assistant 输出与本轮反馈（测试输出 + 当前文件回显）
                messages.append(Message(role="assistant", content=response_content))
                if output_mode == "patch":
                    messages.append(Message(role="user", content=self._build_followup_patch(feedback, current_content)))
                else:
                    messages.append(Message(role="user", content=self._build_followup(feedback, current_content)))
            # ---- Turn 级压缩：chat 前预算检查（超预算即折叠中间轮） ----
            tokens = _messages_tokens(messages)
            if turn == 1 and tokens > max_input_tokens:
                # 首轮降级：按预算反推快照上限重建首轮消息（指令本身过大则失败）。
                system_content = context.system_prompt or self.description
                if output_mode == "patch":
                    bare_prompt = self._build_prompt_patch(request, target_path, "", feedback, repo_map_block)
                else:
                    bare_prompt = self._build_prompt(request, target_path, "", feedback, repo_map_block)
                overhead = _estimate_tokens(system_content) + _estimate_tokens(bare_prompt)
                file_allow = int((max_input_tokens - overhead) * (1 - _FIRST_TURN_RESERVE_RATIO))
                if file_allow <= 0:
                    _finish_session("failed", turns_used, error="首轮提示词超预算且指令本身过大")
                    facts.append({"key": "coding_agent_first_turn_over_budget", "value": tokens})
                    return CapabilityResult(
                        status="failed",
                        error=f"首轮提示词超预算（估算 {tokens} > {max_input_tokens}）且指令本身过大，已拒绝",
                        facts=facts,
                    )
                if output_mode == "patch":
                    degraded_prompt = self._build_prompt_patch(
                        request, target_path, current_content, feedback, repo_map_block, file_allow
                    )
                else:
                    degraded_prompt = self._build_prompt(
                        request, target_path, current_content, feedback, repo_map_block, file_allow
                    )
                messages[1] = Message(role="user", content=degraded_prompt)
                tokens = _messages_tokens(messages)
                facts.append({"key": "coding_agent_first_turn_degraded", "value": file_allow})
            fold_to = turn - 1 - _COMPRESS_KEEP_LAST_TURNS
            if tokens > max_input_tokens and fold_to > folded_through_turn:
                entries = "; ".join(
                    f"turn{n}: {_summarize_turn_facts(facts, n)}"
                    for n in range(1, fold_to + 1)
                )
                fold_text = f"{_FOLD_MARKER}：第1–{fold_to}轮已折叠] {entries}"
                messages = _compress_messages(messages, fold_text)
                folded_through_turn = fold_to
                after = _messages_tokens(messages)
                facts.append({
                    "key": "coding_agent_compressed",
                    "value": f"turns=1-{fold_to} before={tokens} after={after}",
                })
                note_entry = (
                    f"## 压缩事件（第 {turn} 轮前，{time.strftime('%Y-%m-%d %H:%M')}）\n"
                    f"折叠 turn 1–{fold_to}：{entries}\n"
                    f"估算 tokens：{tokens} → {after}（预算 {max_input_tokens}）\n"
                )
                try:
                    note_status = await asyncio.to_thread(
                        _append_session_note, note_filename, note_header, note_entry
                    )
                    facts.append({
                        "key": "coding_agent_note_capped" if note_status == "capped" else "coding_agent_note",
                        "value": note_filename,
                    })
                except Exception:
                    pass
            client = GrantLLMClient(
                context.llm_client,
                context.authorization_manager,
                context.grant_id,
            )
            try:
                started_at = time.monotonic()
                response = await client.chat(
                    provider_name=context.provider_name,
                    messages=messages,
                    model=context.model,
                    temperature=0.0,
                    max_tokens=4000,
                )
                response_content = response.content
            except PermissionError as exc:
                if session_open:
                    try:
                        self.database.record_agent_turn(
                            session_id, turn, outcome="model_denied",
                            detail={"error": str(exc)[:500]},
                        )
                    except Exception:
                        pass
                    _finish_session("failed", turns_used, error=str(exc))
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(status="failed", error=str(exc), facts=facts)
            except Exception as exc:
                if session_open:
                    try:
                        self.database.record_agent_turn(
                            session_id, turn, outcome="model_error",
                            detail={"error": str(exc)[:500]},
                        )
                    except Exception:
                        pass
                    _finish_session("failed", turns_used, error=f"coding_agent 模型调用失败: {exc}")
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"coding_agent 模型调用失败: {exc}",
                    facts=facts,
                )

            # Turn 记录：每轮一次模型调用即一条；后续失败分支回写 outcome。
            turn_row = None
            if session_open:
                turn_usage = response.usage if isinstance(response.usage, dict) else {}
                try:
                    turn_row = self.database.record_agent_turn(
                        session_id, turn, outcome="model_ok",
                        input_tokens=int(turn_usage.get("prompt_tokens") or turn_usage.get("input_tokens") or 0),
                        output_tokens=int(turn_usage.get("completion_tokens") or turn_usage.get("output_tokens") or 0),
                        duration_ms=int((time.monotonic() - started_at) * 1000),
                        detail={"mode": output_mode},
                    )
                except Exception:
                    turn_row = None

            # ---- 输出解析：patch 模式（动作 A：代码内容不进 JSON）与 full 模式分叉 ----
            if output_mode == "patch":
                from patcher import apply_patches, error_feedback_text
                pr = apply_patches(current_content if read_status == "ok" else None, response_content)
                if not pr.ok:
                    err = pr.error
                    _turn_outcome(turn_row, "patch_error", {"kind": err.kind, "message": err.message})
                    if turn < max_turns:
                        # 补丁错误作为反馈进入下一轮；消息回填统一由循环顶部完成
                        feedback = error_feedback_text(pr) or err.message
                        facts.append({"key": f"coding_agent_turn{turn}_patch_error", "value": err.kind})
                        continue
                    _finish_session("failed", turns_used, error=f"第 {turn} 轮补丁未应用: {err.message}")
                    facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                    return CapabilityResult(
                        status="failed",
                        error=f"第 {turn} 轮补丁未应用: {err.message}",
                        facts=facts,
                    )
                new_content = pr.content
                facts.append({"key": f"coding_agent_turn{turn}_blocks_applied", "value": pr.applied})
            else:
                parsed = _parse_result(response.content)
                if parsed is None:
                    # 修复尝试：尾逗号 / 字符串内裸换行等已知噪声（json_repair，保守策略）
                    from json_repair import parse_loose
                    repaired = parse_loose(response.content)
                    if isinstance(repaired, dict):
                        parsed = repaired
                        facts.append({"key": "coding_agent_json_repaired", "value": f"turn{turn}"})
                if parsed is None:
                    _turn_outcome(turn_row, "json_error")
                    _finish_session("failed", turns_used, error=f"第 {turn} 轮返回不是合法 JSON")
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
                    _turn_outcome(turn_row, "invalid_output")
                    _finish_session("failed", turns_used, error=f"第 {turn} 轮输出缺少 content 字段")
                    facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                    return CapabilityResult(
                        status="failed",
                        error=f"第 {turn} 轮输出缺少 content 字段",
                        facts=facts,
                    )
                if not isinstance(new_content, str):
                    _turn_outcome(turn_row, "invalid_output")
                    _finish_session("failed", turns_used, error=f"第 {turn} 轮的 content 不是字符串")
                    facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                    return CapabilityResult(
                        status="failed",
                        error=f"第 {turn} 轮的 content 不是字符串",
                        facts=facts,
                    )

                # 写入位置由 Runtime 决定；模型声明的 path 只能与目标一致。
                if proposed_path and str(proposed_path).replace("\\", "/") != target_path.replace("\\", "/"):
                    _turn_outcome(turn_row, "path_redirect", {"proposed": str(proposed_path)})
                    _finish_session("failed", turns_used, error=f"模型试图写入非授权路径: {proposed_path}")
                    facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                    return CapabilityResult(
                        status="failed",
                        error=f"模型试图写入非授权路径: {proposed_path}（目标: {target_path}）",
                        facts=facts,
                    )

            # ---- 空内容防护：向已有文件写入空内容是破坏性操作 ----
            if isinstance(new_content, str) and not new_content.strip():
                if turn < max_turns:
                    _turn_outcome(turn_row, "empty_rejected")
                    if output_mode == "patch":
                        feedback = (
                            f"第 {turn} 轮的补丁会把目标文件清空，已被拒绝。"
                            "请基于文件当前内容重新输出补丁块（不要删除全部内容）。"
                        )
                    else:
                        feedback = (
                            f"第 {turn} 轮输出的 content 为空。向目标文件写入空内容是破坏性操作、已被拒绝。"
                            "请重新输出目标文件的完整非空内容。"
                        )
                    # 消息回填统一交给循环顶部（此处重复追加会让 assistant 消息出现两次）
                    continue
                _turn_outcome(turn_row, "empty_rejected_final")
                _finish_session("failed", turns_used, error=f"第 {turn} 轮输出为空内容，已拒绝写入（文件保持原状）")
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
                _turn_outcome(turn_row, "write_denied", {"error": str(write_error)[:500]})
                _finish_session("failed", turns_used, error=f"coding_agent 写入被拒绝: {write_error}")
                facts.append({"key": "coding_agent_turns_used", "value": turns_used})
                return CapabilityResult(
                    status="failed",
                    error=f"coding_agent 写入被拒绝: {write_error}",
                    facts=facts,
                )
            _turn_outcome(turn_row, "applied")
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
                _turn_outcome(turn_row, "selftest_passed", {"exit_code": 0})
                facts.append({"key": "coding_agent_selftest", "value": "passed"})
                break
            _turn_outcome(turn_row, "selftest_failed", {"exit_code": last_test_exit})
            retry_line = (
                "请修复问题后重新输出补丁块（基于文件当前内容）。"
                if output_mode == "patch"
                else "请修复问题后重新输出完整文件内容。"
            )
            feedback = (
                f"第 {turn} 轮修改后自测未通过（exit_code={last_test_exit}）。\n"
                f"测试输出摘要：\n{_tail(tr_result.get('stderr') or tr_result.get('stdout') or '', _MAX_FEEDBACK_CHARS)}\n"
                f"{retry_line}"
            )
            facts.append({"key": f"coding_agent_turn{turn}_test_exit", "value": last_test_exit})

        grant = context.authorization_manager.get(context.grant_id)
        facts.append({"key": "coding_agent_turns_used", "value": turns_used})
        if last_test_exit is not None:
            facts.append({"key": "coding_agent_last_test_exit", "value": last_test_exit})
        _finish_session("completed", turns_used)
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
        max_file_chars: int = _MAX_FILE_CONTEXT_CHARS,
    ) -> str:
        inputs = request.inputs or {}
        instruction = str(inputs.get("instruction") or request.goal)
        if current_content:
            if len(current_content) > max_file_chars:
                current_content = current_content[:max_file_chars] + "\n...[内容已截断]"
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
    def _build_prompt_patch(
        request: CapabilityRequest,
        target_path: str,
        current_content: str,
        feedback: str = "",
        repo_map_block: str = "",
        max_file_chars: int = _MAX_FILE_CONTEXT_CHARS,
    ) -> str:
        """patch 模式首轮提示词：search/replace 补丁块契约（代码内容不进 JSON）。"""
        inputs = request.inputs or {}
        instruction = str(inputs.get("instruction") or request.goal)
        if current_content:
            if len(current_content) > max_file_chars:
                current_content = current_content[:max_file_chars] + "\n...[内容已截断]"
            current_block = f"文件当前内容：\n```\n{current_content}\n```"
        else:
            current_block = "文件当前不存在，需要新建（用空 SEARCH 段）。"
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
            "用 search/replace 补丁块输出你的修改；不要输出 JSON、不要输出解释文字、"
            "不要用 markdown 代码围栏包裹补丁块。块格式：\n"
            "\n"
            "<<<<<<< SEARCH\n"
            "（要被替换的现有内容，必须与文件当前内容逐字一致且全文唯一；新建文件时此段留空）\n"
            "=======\n"
            "（替换后的内容；删除片段时此段留空）\n"
            ">>>>>>> REPLACE\n"
            "\n"
            "规则：SEARCH 必须逐字命中（含缩进与换行）且在全文只出现一次；"
            "可以输出多个补丁块（按顺序应用）；只改必须改的行，不要重排无关代码。"
        )

    @staticmethod
    def _build_followup_patch(feedback: str, current_content: str) -> str:
        """patch 模式后续轮 user 消息：反馈 + 磁盘当前文件回显（前缀缓存友好）。"""
        if len(current_content) > _MAX_FILE_CONTEXT_CHARS:
            current_content = current_content[:_MAX_FILE_CONTEXT_CHARS] + "\n...[内容已截断]"
        return (
            "上一轮反馈：\n"
            f"{feedback}\n"
            "文件当前内容（以磁盘为准）：\n"
            f"```\n{current_content}\n```\n"
            "请基于当前内容再次输出 search/replace 补丁块（格式同前，不要 JSON）。"
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
