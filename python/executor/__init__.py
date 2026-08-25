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


class DefaultExecutor(ExecutorAI):
    """默认Executor实现"""

    # 结果校验的三种状态。
    # 区分「校验不通过」与「无法校验」很重要：
    # 前者应判定步骤失败，后者不能默默当成通过（否则校验层等于不存在）。
    CHECK_PASSED = "passed"
    CHECK_FAILED = "failed"
    CHECK_UNVERIFIED = "unverified"

    def __init__(self, llm_client: LLMClient, model_config: Dict[str, Any], tool_registry=None) -> None:
        self.llm_client = llm_client
        self.model_config = model_config
        self.tool_registry = tool_registry if tool_registry else get_registry()
    
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
    
    async def execute_step(self, step: Step) -> Dict[str, Any]:
        """执行单个步骤"""
        
        print(f"  执行步骤 {step.order}: {step.description}")
        print(f"    工具: {step.tool}")
        print(f"    输入: {step.inputs}")
        
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
            result = await asyncio.to_thread(
                self.tool_registry.execute_tool, tool_id, instruction, step.inputs
            )
            
            if result.get("success"):
                print(f"    [OK] 工具执行成功: {result.get('message')}")

                # 检查结果是否符合预期
                check_status = await self.check_result(step, result)

                # 校验明确失败 -> 步骤失败。
                # 工具跑完不等于做对了事（例如写入了错的内容、下载到 404 页面）。
                if check_status == self.CHECK_FAILED:
                    print(f"    [FAIL] 结果校验未通过: {step.check_criteria}")
                    return {
                        "success": False,
                        "output": result,
                        "check_status": check_status,
                        "check_passed": False,
                        "error": f"结果校验未通过（标准：{step.check_criteria}）",
                    }

                if check_status == self.CHECK_UNVERIFIED:
                    print(f"    [WARN] 结果未能校验，步骤标记为需人工复核")

                return {
                    "success": True,
                    "output": result,
                    "check_status": check_status,
                    "check_passed": check_status == self.CHECK_PASSED,
                    "error": None,
                }
            else:
                print(f"    [FAIL] 失败: {result.get('message')}")
                return {
                    "success": False,
                    "output": result,
                    "check_status": self.CHECK_UNVERIFIED,
                    "check_passed": False,
                    "error": result.get("error", "Unknown error"),
                }

        except Exception as e:
            print(f"    [FAIL] 异常: {str(e)}")
            return {
                "success": False,
                "output": None,
                "check_status": self.CHECK_UNVERIFIED,
                "check_passed": False,
                "error": str(e),
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
            print(f"    [WARN] 校验配置缺失（model_config 缺 {e}），本步骤标记为未校验")
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
            print(f"    [WARN] 校验回复无法解析: {verdict!r}，标记为未校验")
            return self.CHECK_UNVERIFIED

        except Exception as e:
            print(f"    [WARN] 校验调用失败，标记为未校验: {e}")
            return self.CHECK_UNVERIFIED
