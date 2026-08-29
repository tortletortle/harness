"""
Executor - 执行器

职责：
1. 执行单个步骤
2. 检查执行结果
3. 处理错误
"""

from typing import Any, Dict
import asyncio
from pathlib import Path
from datetime import datetime

from models import Step, StepStatus
from utils.interfaces import ExecutorAI
from llm_client import LLMClient, Message
from tools import get_registry
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

    def __init__(self, llm_client: LLMClient, model_config: Dict[str, Any], tool_registry=None, database=None) -> None:
        self.llm_client = llm_client
        self.model_config = model_config
        self.tool_registry = tool_registry if tool_registry else get_registry()
        # 可选：有则写入审计日志（谁/何时/哪个工具指令/结果），
        # 未传入时静静跳过，不影响现有调用方不传参的兼容性。
        self.database = database
        # 支持通过 model_config 覆盖默认超时，未配置则用类默认值。
        self.step_timeout = model_config.get("step_timeout", self.DEFAULT_STEP_TIMEOUT) if model_config else self.DEFAULT_STEP_TIMEOUT

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
            result = await self.execute_step(Step(**request["step"]))
            return result
        elif action == "check_result":
            passed = await self.check_result(
                Step(**request["step"]),
                request["output"],
            )
            return {"passed": passed}
        else:
            raise ValueError(f"Unknown action: {action}")

    async def health_check(self) -> bool:
        return True

    def _audit(self, tool_id: str, instruction: str, inputs: Dict[str, Any], success, blocked: bool, block_reason: str = None, step: Step = None):
        """写入审计日志，失败不影响主流程"""
        if not self.database:
            return
        try:
            self.database.save_audit_log(
                tool_id=tool_id,
                instruction=instruction,
                inputs=inputs,
                step_id=str(step.step_id) if step else None,
                success=success,
                blocked=blocked,
                block_reason=block_reason,
                actor="executor",
            )
        except Exception as e:
            logger.warning("审计日志写入失败: {}", e)

    async def execute_step(self, step: Step) -> Dict[str, Any]:
        """执行单个步骤"""

        logger.info("执行步骤 {}: {}", step.order, step.description)
        logger.debug("工具: {}", step.tool)
        logger.debug("输入: {}", step.inputs)

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

            # 执行工具
            # execute_tool 是同步的，内部会跑 subprocess（command_runner 默认超时 60s）。
            # 直接调用会堵住整个事件循环，期间 API 和 WebSocket 全部无响应（前端表现为假死）。
            # 外层再包一层 wait_for 作为统一熔断：即便某个工具自身的超时机制失效或
            # 卡在系统调用上，也不会无限期挂住整个执行循环。
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.tool_registry.execute_tool, tool_id, instruction, step.inputs
                    ),
                    timeout=self.step_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("步骤执行超时（{}秒）", self.step_timeout)
                self._audit(
                    tool_id=tool_id,
                    instruction=instruction,
                    inputs=step.inputs,
                    success=False,
                    blocked=False,
                    block_reason="Step execution timeout",
                    step=step,
                )
                return {
                    "success": False,
                    "output": None,
                    "check_status": self.CHECK_UNVERIFIED,
                    "check_passed": False,
                    "error": "Step execution timeout",
                    "blocked": False,
                    # P2 错误分类：超时多为瞬时负载，重试可能成功
                    "retryable": True,
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
                inputs=step.inputs,
                success=result.get("success"),
                blocked=blocked,
                block_reason=error_code if blocked else None,
                step=step,
            )

            # P0-b 第二道守卫（OpenHands run_action 双闸思路）：
            # 工具声称成功但结果带拦截标志 → 不可信，按拦截失败处理。
            if result.get("success") and blocked:
                logger.error("工具返回 success 但携带拦截标志，按拦截处理: {}", error_code)
                result = {**result, "success": False, "error": error_code or "Blocked"}

            if result.get("success"):
                logger.info("工具执行成功: {}", result.get("message"))

                # 检查结果是否符合预期
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
                logger.error("失败: {}", result.get("message"))
                return {
                    "success": False,
                    "output": result,
                    "check_status": self.CHECK_UNVERIFIED,
                    "check_passed": False,
                    "error": result.get("error", "Unknown error"),
                    # 安全拦截（危险命令/非法包名/非法URL/工作目录越界）重试无意义，
                    # 无论重试多少次结果都不会变。
                    "blocked": blocked,
                    # P2 错误分类：拦截不可重试；其余失败（网络/依赖缺失等）可重试
                    "retryable": not blocked,
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
                "retryable": True,
            }

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

        # 默认返回 None，让调用者处理
        return None


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
