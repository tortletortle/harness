"""
Executor - 执行器

职责：
1. 执行单个步骤
2. 检查执行结果
3. 处理错误
"""

from typing import Any, Dict, Optional
import asyncio
from pathlib import Path
from datetime import datetime

from models import Step, StepStatus
from utils.interfaces import ExecutorAI
from llm_client import LLMClient, Message
from tools import get_registry
from acceptance_runner import AcceptanceRunner
from authorization import AuthorizationManager
from agent_tools import AgentRuntimeContext
from capabilities import CapabilityRegistry, CapabilityRequest
from logging_config import get_logger

logger = get_logger(__name__)

# P0-b：工具安全拦截的结构化判定。
# 工具结果现在应携带 "blocked": True 字段；下面的错误码仅为兼容旧工具/
# 第三方插件的回退路径（OpenHands 式双层防御：结构化字段优先，文案兜底）。
LEGACY_BLOCKED_ERROR_CODES = (
    "Dangerous command blocked",
    "Invalid package name",
    "Invalid or blocked URL",
    "Workdir not allowed",
)


def _is_blocked_result(result: Dict[str, Any]) -> bool:
    """判断工具结果是否为安全拦截。结构化字段优先，错误码回退。"""
    if result.get("blocked") is True:
        return True
    return result.get("error") in LEGACY_BLOCKED_ERROR_CODES


class DefaultExecutor(ExecutorAI):
    """默认Executor实现"""

    # 结果校验的三种状态。
    # 区分「校验不通过」与「无法校验」很重要：
    # 前者应判定步骤失败，后者不能默默当成通过（否则校验层等于不存在）。
    CHECK_PASSED = "passed"
    CHECK_FAILED = "failed"
    CHECK_UNVERIFIED = "unverified"

    # 步骤级超时炙断的默认阈值（秒）。覆盖了各工具自身超时（command_runner 60s /
    # web_fetcher 30s 等）再留一些余量，兑底那些没有自身超时保护的工具
    # （file_creator / text_editor / manual_tool）或自身超时机制失效的情况。
    DEFAULT_STEP_TIMEOUT = 120

    def __init__(self, llm_client: LLMClient, model_config: Dict[str, Any], tool_registry=None, database=None, authorization_manager: AuthorizationManager = None, capability_registry: Optional[CapabilityRegistry] = None) -> None:
        self.llm_client = llm_client
        self.model_config = model_config
        self.tool_registry = tool_registry if tool_registry else get_registry()
        # 可选：有则写入审计日志（谁/何时/哪个工具指令/结果），
        # 未传入时静静跳过，不影响现有调用方不传参的兼容性。
        self.database = database
        # Runtime 注入时启用调用前 Grant 校验；直接使用 Executor 的旧调用
        # 不传 manager，保持旧接口兼容（真实 API 路径始终传入）。
        self.authorization_manager = authorization_manager
        # 可选：统一 Capability 清单。kind=agent_tool 的步骤分发给
        # Capability 契约执行（单轮模型 + Grant 受限内部工具），
        # 结果并入下方同一验收路径；不传时仅支持普通工具（旧接口兼容）。
        self.capability_registry = capability_registry
        # 确定性验收器：由执行器持有，结构化 acceptance 规则优先于 LLM 自检。
        self.acceptance_runner = AcceptanceRunner()
        # 支持通过 model_config 覆盖默认超时，未配置则用类默认值。
        self.step_timeout = model_config.get("step_timeout", self.DEFAULT_STEP_TIMEOUT) if model_config else self.DEFAULT_STEP_TIMEOUT
        # agent 步骤包含一次真实模型调用，超时阈值单独放宽。
        self.agent_step_timeout = model_config.get("agent_step_timeout", 300) if model_config else 300

    @property
    def name(self) -> str:
        return "executor"

    @property
    def version(self) -> str:
        return "0.1.0"

    async def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """统一处理入口"""
        action = request.get("action")

        if action == "execute_step":
            result = await self.execute_step(
                Step(**request["step"]),
                grant_id=request.get("grant_id"),
                plan_id=request.get("plan_id"),
            )
            return result
        elif action == "check_result":
            step = Step(**request["step"])
            if step.acceptance:
                acceptance = await self._run_acceptance(step, request["output"])
                return {
                    "check_status": acceptance["status"],
                    "check_passed": acceptance["passed"],
                    "acceptance": acceptance,
                    # 保留旧字段，但改为真正的 bool，避免名称与值语义冲突。
                    "passed": acceptance["passed"],
                }
            check_status = await self.check_result(step, request["output"])
            return {
                "check_status": check_status,
                "check_passed": check_status == self.CHECK_PASSED,
                "passed": check_status == self.CHECK_PASSED,
            }
        else:
            raise ValueError(f"Unknown action: {action}")

    async def health_check(self) -> bool:
        return True

    def _audit(self, tool_id: str, instruction: str, inputs: Dict[str, Any], success, blocked: bool, block_reason: str = None, step: Step = None, grant_id: str = None, plan_id: str = None):
        """写入审计日志，失败不影响主流程"""
        if not self.database:
            return
        try:
            self.database.save_audit_log(
                tool_id=tool_id,
                instruction=instruction,
                inputs=inputs,
                plan_id=plan_id,
                step_id=str(step.step_id) if step else None,
                success=success,
                blocked=blocked,
                block_reason=block_reason,
                actor="executor",
                grant_id=grant_id,
            )
        except Exception as e:
            logger.warning("审计日志写入失败: {}", e)

    async def execute_step(self, step: Step, grant_id: str = None, plan_id: str = None) -> Dict[str, Any]:
        """执行单个步骤；grant_id 存在时先经过 Runtime 授权。"""

        logger.info("执行步骤 {}: {}", step.order, step.description)
        logger.debug("工具: {}", step.tool)
        logger.debug("输入: {}", step.inputs)

        from retry_policy import automatic_retry_policy
        retry_policy = automatic_retry_policy(getattr(step, "tool", ""), self.capability_registry, self.tool_registry)
        try:
            # 解析工具ID和指令
            tool_parts = step.tool.split(":")
            if len(tool_parts) == 2:
                tool_id = tool_parts[0]
                instruction = tool_parts[1]
            else:
                # 如果没有指定指令，尝试从 inputs 中获取
                tool_id = step.tool
                instruction = step.inputs.get("instruction")

                # 如果没有 instruction，尝试 action 键（常见的别名）
                if not instruction:
                    instruction = step.inputs.get("action")

                # 如果还是没有，根据工具类型推断默认指令
                if not instruction:
                    instruction = self._infer_instruction(tool_id, step.inputs)

                # 最后使用默认值
                if not instruction:
                    instruction = "execute"

            # Runtime Grant 调用前授权闸门。拒绝时不进入线程、不触发底层工具。
            effective_inputs = dict(step.inputs or {})
            grant = self.authorization_manager.get(grant_id) if self.authorization_manager and grant_id else None
            if grant is not None:
                if tool_id in {"command_runner", "package_installer", "github_operator"} and not effective_inputs.get("cwd"):
                    # 未指定 cwd 的命令/包/gh 操作固定在本次 Grant 的 workspace 中，
                    # 避免工具落到 API 进程当前目录；显式 cwd 仍由资源检查约束。
                    effective_inputs["cwd"] = grant.workspace_root
                elif tool_id == "git_operator" and not effective_inputs.get("path"):
                    # Git 工具的默认 path="." 由工具进程解释，必须显式绑定到 Grant workspace。
                    effective_inputs["path"] = grant.workspace_root
            if tool_id not in ("browser_observe", "super_search", "pdf_reader", "image_reader", "office_reader", "desktop_observe", "file_find", "document_batch") and self.authorization_manager is not None and (
                grant_id is not None or self.authorization_manager.require_grant
            ):
                decision = self.authorization_manager.authorize(
                    grant_id, tool_id, instruction, effective_inputs,
                )
                if not decision.allowed:
                    self._audit(
                        tool_id=tool_id,
                        instruction=instruction,
                        inputs=effective_inputs,
                        success=False,
                        blocked=True,
                        block_reason=decision.reason,
                        step=step,
                        grant_id=grant_id,
                        plan_id=plan_id,
                    )
                    return {
                        "success": False,
                        "output": None,
                        "check_status": self.CHECK_UNVERIFIED,
                        "check_passed": False,
                        "error": decision.message,
                        "blocked": True,
                        "blocked_reason": decision.reason,
                        "retryable": False,
                        "grant_id": grant_id,
                        "grant_usage": decision.usage,
                    }

            # 执行：agent_tool 步骤走统一 Capability 契约（单轮模型 + Grant
            # 受限内部工具），普通步骤走同步工具线程。
            # execute_tool 是同步的，内部会跑 subprocess（command_runner 默认超时 60s）。
            # 直接调用会堵住整个事件循环，期间 API 和 WebSocket 全部无响应（前端表现为假死）。
            # 外层再包一层 wait_for 作为统一熔断：即便某个工具自身的超时机制失效或
            # 卡在系统调用上，也不会无限期挂住整个执行循环。
            agent_capability = self._agent_capability(tool_id)
            try:
                if agent_capability is not None:
                    result = await asyncio.wait_for(
                        self._run_agent_capability(
                            agent_capability, step, tool_id, effective_inputs, grant_id, plan_id
                        ),
                        timeout=max(self.step_timeout, self.agent_step_timeout),
                    )
                elif tool_id in ("browser_observe", "super_search", "pdf_reader", "image_reader", "office_reader", "desktop_observe", "file_find", "document_batch"):
                    # 浏览器桥拥有一次完整授权；不在此提前消费同一个 Grant 的调用额度。
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.tool_registry.execute_tool_with_grant, tool_id, instruction, effective_inputs,
                            authorization_manager=self.authorization_manager, grant_id=grant_id,
                        ),
                        timeout=self.step_timeout,
                    )
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.tool_registry.execute_tool, tool_id, instruction, effective_inputs
                        ),
                        timeout=self.step_timeout,
                    )
            except asyncio.TimeoutError:
                logger.error("步骤执行超时（{}秒）", self.step_timeout)
                self._audit(
                    tool_id=tool_id,
                    instruction=instruction,
                    inputs=effective_inputs,
                    success=False,
                    blocked=False,
                    block_reason="Step execution timeout",
                    step=step,
                    grant_id=grant_id,
                    plan_id=plan_id,
                )
                return {
                    "success": False,
                    "output": None,
                    "check_status": self.CHECK_UNVERIFIED,
                    "check_passed": False,
                    "error": "Step execution timeout",
                    "blocked": False,
                    # P2 错误分类：超时多为瞬时负载，重试可能成功
                    "retryable": retry_policy["eligible"],
                    "retry_policy": retry_policy,
                    "manual_retry_required": not retry_policy["eligible"],
                }

            # P0-b：结构化拦截判定（字段优先，错误码回退）
            blocked = _is_blocked_result(result)
            # 审计：记录本次工具调用及其结果（包括安全拦截：Dangerous command blocked /
            # Invalid package name / Invalid or blocked URL / Workdir not allowed）。
            error_code = None
            if not result.get("success"):
                error_code = result.get("error")
            self._audit(
                tool_id=tool_id,
                instruction=instruction,
                inputs=effective_inputs,
                success=result.get("success"),
                blocked=blocked,
                block_reason=error_code if blocked else None,
                step=step,
                grant_id=grant_id,
                plan_id=plan_id,
            )

            # P0-b 第二道守卫（OpenHands run_action 双闸思路）：
            # 工具声称成功但结果带拦截标志 → 不可信，按拦截失败处理。
            if result.get("success") and blocked:
                logger.error("工具返回 success 但携带拦截标志，按拦截处理: {}", error_code)
                result = {**result, "success": False, "error": error_code or "Blocked"}

            if result.get("success"):
                logger.info("工具执行成功: {}", result.get("message"))

                # 结构化 acceptance 走独立确定性验收；旧版空 acceptance
                # 继续走 LLM check_result，保持历史计划兼容。
                if step.acceptance:
                    acceptance = await self._run_acceptance(step, result)
                    if not acceptance["passed"]:
                        logger.warning("确定性验收未通过: {}", step.acceptance)
                        return {
                            "success": False,
                            "output": result,
                            "check_status": self.CHECK_FAILED,
                            "check_passed": False,
                            "error": "确定性验收未通过",
                            "blocked": False,
                            "retryable": False,
                            "acceptance_status": acceptance["status"],
                            "acceptance_result": acceptance,
                            "acceptance_evidence": acceptance["evidence"],
                            "reward": acceptance["reward"],
                            "acceptance_failed": True,
                        }
                    return {
                        "success": True,
                        "output": result,
                        "check_status": self.CHECK_PASSED,
                        "check_passed": True,
                        "error": None,
                        "blocked": False,
                        "retryable": False,
                        "acceptance_status": acceptance["status"],
                        "acceptance_result": acceptance,
                        "acceptance_evidence": acceptance["evidence"],
                        "reward": acceptance["reward"],
                        "acceptance_failed": False,
                    }

                # 兼容旧版字符串 check_criteria 的 LLM 验收
                check_status = await self.check_result(step, result)

                # 校验明确失败 -> 步骤失败。
                # 工具跑完不等于做对了事（例如写入了错的内容、下载到 404 页面）。
                if check_status == self.CHECK_FAILED:
                    logger.warning("结果校验未通过: {}", step.check_criteria)
                    return {
                        "success": False,
                        "output": result,
                        "check_status": check_status,
                        "check_passed": False,
                        "error": f"结果校验未通过（标准：{step.check_criteria}）",
                        # 校验失败是逻辑错误，不是网络抖动之类的瞬时故障，重试没有意义。
                        "blocked": True,
                        # P2 错误分类：逻辑校验失败不可重试
                        "retryable": False,
                    }

                if check_status == self.CHECK_UNVERIFIED:
                    logger.warning("结果未能校验，步骤标记为需人工复核")

                return {
                    "success": True,
                    "output": result,
                    "check_status": check_status,
                    "check_passed": check_status == self.CHECK_PASSED,
                    "error": None,
                    "blocked": False,
                    "retryable": False,
                }
            else:
                logger.error("失败: {}", result.get("message") or result.get("error") or "Unknown error")
                return {
                    "success": False,
                    "output": result,
                    "check_status": self.CHECK_UNVERIFIED,
                    "check_passed": False,
                    "error": result.get("error") or result.get("message") or "Unknown error",
                    # 安全拦截（危险命令/非法包名/非法URL/工作目录越界）重试无意义，
                    # 无论重试多少次结果都不会变。
                    "blocked": blocked,
                    # P2 错误分类：拦截不可重试；其余失败（网络/依赖缺失等）可重试
                    "retryable": not blocked and retry_policy["eligible"],
                    "retry_policy": retry_policy,
                    "manual_retry_required": not retry_policy["eligible"],
                }

        except Exception as e:
            logger.exception("异常: {}", e)
            return {
                "success": False,
                "output": None,
                "check_status": self.CHECK_UNVERIFIED,
                "check_passed": False,
                "error": str(e),
                "blocked": False,
                # P2 错误分类：未知异常按可重试处理，由重试上限兜底
                "retryable": retry_policy["eligible"],
                    "retry_policy": retry_policy,
                    "manual_retry_required": not retry_policy["eligible"],
            }

    def _agent_capability(self, tool_id: str):
        """返回 tool_id 对应的 agent_tool Capability；不存在或非 agent 类型时返回 None。"""
        if self.capability_registry is None:
            return None
        capability = self.capability_registry.get(tool_id)
        if capability is None:
            return None
        try:
            kind = capability.spec().kind
        except Exception:
            return None
        return capability if kind == "agent_tool" else None

    async def _run_agent_capability(
        self,
        capability,
        step: Step,
        tool_id: str,
        inputs: Dict[str, Any],
        grant_id: Optional[str],
        plan_id: str = "",
    ) -> Dict[str, Any]:
        """把 agent_tool Capability 接入步骤执行：

        - Runtime Grant（grant_id）原样透传，agent 内部所有副作用
          （模型调用、内部工具）都走同一 Grant 的预算与资源检查；
        - 返回值对齐工具结果字典（success/output/error），使下方
          验收/校验/审计路径无需区分普通工具与 Agent。
        """
        if self.authorization_manager is None:
            return {
                "success": False,
                "output": None,
                "error": "agent 步骤需要 Runtime Grant 环境（authorization_manager 未配置）",
                "blocked": False,
            }
        if not grant_id:
            return {
                "success": False,
                "output": None,
                "error": "agent 步骤必须携带 Runtime Grant",
                "blocked": True,
                "blocked_reason": "grant_required",
            }
        request = CapabilityRequest(
            work_item_id=str(step.step_id),
            goal=step.description,
            inputs=inputs,
            artifact_refs=[],
            allowed_tools=list(inputs.get("allowed_tools") or []),
            acceptance=list(step.acceptance or []),
            grant_id=grant_id,
        )
        try:
            provider = self.model_config["primary"]["provider"]
            model = self.model_config["primary"]["model"]
        except (KeyError, TypeError):
            return {
                "success": False,
                "output": None,
                "error": "agent 执行缺少模型配置（models.executor.primary）",
                "blocked": False,
            }
        context = AgentRuntimeContext(
            llm_client=self.llm_client,
            authorization_manager=self.authorization_manager,
            provider_name=provider,
            model=model,
            grant_id=grant_id,
            system_prompt=str(inputs.get("system_prompt") or ""),
            plan_id=str(plan_id or ""),
        )
        cap_result = await self.capability_registry.run(tool_id, request, context)
        agent_output = (
            cap_result.output
            if isinstance(cap_result.output, dict)
            else ({"result": cap_result.output} if cap_result.output is not None else {})
        )
        result: Dict[str, Any] = {
            "success": cap_result.status == "completed",
            "output": {"agent_status": cap_result.status, **agent_output},
            "message": f"agent {tool_id} -> {cap_result.status}",
            "error": cap_result.error,
            "blocked": False,
            "agent_facts": cap_result.facts,
            "agent_artifacts": cap_result.artifacts,
        }
        if cap_result.usage:
            result["grant_usage"] = cap_result.usage
        return result

    def _infer_instruction(self, tool_id: str, inputs: Dict[str, Any]) -> str:
        """
        根据工具类型和输入推断默认指令

        Args:
            tool_id: 工具ID
            inputs: 输入参数

        Returns:
            推断的指令名
        """
        # file_creator 的映射（最常用，放最前面）
        if tool_id == "file_creator":
            path = inputs.get("path", "")
            # 检查是否要创建目录（路径没有扩展名，或者明确标记为目录）
            if not path or path.endswith("/") or path.endswith("\\"):
                return "create_directory"
            # 如果路径看起来像目录（没有扩展名）
            if "." not in Path(path).name:
                return "create_directory"
            # 默认创建文件
            return "create_file"

        # git_operator 的映射
        elif tool_id == "git_operator":
            action = inputs.get("action", "")
            if action == "init":
                return "git_init"
            elif action == "add":
                return "git_add"
            elif action == "commit":
                return "git_commit"
            elif action == "clone":
                return "git_clone"
            # 默认为 git_init
            return "git_init"

        # package_installer 的映射
        elif tool_id == "package_installer":
            # 检查是否有 package 字段，如果有则需要判断类型
            if "package" in inputs or "packages" in inputs:
                # 默认假设是 npm（因为很多前端项目用 npm）
                return "install_npm"
            elif "requirements_file" in inputs:
                return "install_pip"
            # 默认 npm
            return "install_npm"

        # text_editor 的映射
        elif tool_id == "text_editor":
            if "search" in inputs and "replace" in inputs:
                return "replace_in_file"
            elif "old_text" in inputs and "new_text" in inputs:
                return "replace_in_file"
            elif "content" in inputs or "code" in inputs:
                return "append_to_file"
            elif "line_number" in inputs:
                return "insert_at_line"
            # 默认为读取
            return "read_file"

        # command_runner 的映射
        elif tool_id == "command_runner":
            return "run_command"

        # web_fetcher 的映射
        elif tool_id == "web_fetcher":
            if "path" in inputs or "output" in inputs or "save_to" in inputs:
                return "download_file"
            else:
                return "fetch_content"

        # super_search 的映射
        elif tool_id == "super_search":
            if "url" in inputs and "query" not in inputs:
                return "extract_text"
            if inputs.get("fetch_top") or inputs.get("content_chars"):
                return "deep_search"
            return "search"

        # code_context 的映射
        elif tool_id == "code_context":
            if "note" in inputs or "content" in inputs:
                return "remember"
            if inputs.get("force") or inputs.get("reindex"):
                return "reindex"
            if "query" in inputs:
                return "search"
            return "repo_map"

        # lint_gate 的映射
        elif tool_id == "lint_gate":
            return "lint_file"

        # github_operator 的映射
        elif tool_id == "github_operator":
            action = str(inputs.get("action") or "").strip()
            if action in ("pr_create", "pr_status", "pr_checks", "pr_list"):
                return action
            return "pr_status"

        # http_api 的映射
        elif tool_id == "http_api":
            return "send_request"

        # browser_check 的映射
        elif tool_id == "browser_check":
            return "check_page"

        # 默认返回 None，让调用者处理
        return None


    async def _run_acceptance(self, step: Step, tool_result: Dict[str, Any]) -> Dict[str, Any]:
        """运行结构化确定性验收；不调用 LLM。"""
        result = await asyncio.to_thread(
            self.acceptance_runner.run,
            step.acceptance,
            tool_result,
        )
        return result.to_dict()

    async def check_result(self, step: Step, output: Dict[str, Any]) -> str:
        """
        检查执行结果是否符合预期。

        Returns:
            CHECK_PASSED     — 确认符合标准
            CHECK_FAILED     — 确认不符合标准
            CHECK_UNVERIFIED — 无法得出结论（无标准、配置缺失、LLM 调用失败）

        注意：此处不再把异常当成「通过」。调用方需自行决定如何处理 UNVERIFIED。
        """

        # 没有 check_criteria 或明确标为人工检查：不属于自动校验范围
        if not step.check_criteria or step.check_criteria == "手动检查":
            return self.CHECK_UNVERIFIED

        # 配置缺失时显式报告，不静默放行
        try:
            provider = self.model_config["primary"]["provider"]
            model = self.model_config["primary"]["model"]
        except (KeyError, TypeError) as e:
            logger.warning("校验配置缺失（model_config 缺 {}），本步骤标记为未校验", e)
            return self.CHECK_UNVERIFIED

        try:
            system_prompt = """你是一个结果验证专家。
根据检查标准，判断执行结果是否符合预期。
只回复 'YES' 或 'NO'。"""

            user_content = f"""检查标准：{step.check_criteria}

执行结果：{output}

是否符合标准？"""

            response = await self.llm_client.chat(
                provider_name=provider,
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_content),
                ],
                model=model,
                temperature=0.1,
                max_tokens=10,
            )

            verdict = response.content.strip().upper()

            if "YES" in verdict or "成功" in verdict or "通过" in verdict:
                return self.CHECK_PASSED
            if "NO" in verdict or "失败" in verdict or "不符合" in verdict:
                return self.CHECK_FAILED

            # 回复无法解析，不猜
            logger.warning("校验回复无法解析: {!r}，标记为未校验", verdict)
            return self.CHECK_UNVERIFIED

        except Exception as e:
            logger.warning("校验调用失败，标记为未校验: {}", e)
            return self.CHECK_UNVERIFIED
