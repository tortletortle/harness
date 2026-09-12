"""
工具库索引
管理所有可用工具，并提供AI agent处理复杂请求
"""

from typing import Dict, Any, List, Optional
import asyncio
import importlib.util
import inspect
import json
import os
from pathlib import Path
from .file_creator import FileCreator
from .command_runner import CommandRunner
from .package_installer import PackageInstaller
from .manual_tool import ManualTool
from .git_operator import GitOperator
from .text_editor import TextEditor
from .web_fetcher import WebFetcher
from .super_search import SuperSearch
from .code_context import CodeContext
from .test_runner import TestRunner
from .lint_gate import LintGateTool
from .github_operator import GitHubOperator
from .http_api import HttpApi
from .browser_check import BrowserCheck
from .project_memory import ProjectMemory

from logging_config import get_logger

logger = get_logger(__name__)


class ToolRegistryAgent:
    """
    工具库AI Agent
    
    负责处理复杂的工具请求，带有上下文管理系统。
    根据任务类型使用不同的上下文。
    """
    
    def __init__(self, llm_client, model_config: Dict[str, Any], registry=None):
        """
        初始化工具库AI Agent
        
        Args:
            llm_client: LLM客户端
            model_config: 模型配置
            registry: 所属 ToolRegistry（回引，用于授权/审计；设计稿 §23.3）
        """
        self.llm_client = llm_client
        self.model_config = model_config
        self.registry = registry
        
        # 上下文配置
        self.contexts = {
            "tool_manager": """你是一个工具库管理员。你负责管理和介绍工具库中的所有工具。

你的职责：
1. 提供工具的详细信息
2. 解释工具的功能和用途
3. 帮助用户理解如何使用工具
""",
            "search_assistant": """你是一个搜索助手。你负责调用多个搜索工具，整理信息，并返回有用的结果。

你的职责：
1. 理解搜索需求
2. 调用合适的搜索工具（如super_search）
3. 整理和总结搜索结果
4. 返回清晰、有用的信息

注意：你应该调用多个搜索源，综合信息后返回。
""",
        }
    
    def get_context(self, task_type: str) -> str:
        """
        根据任务类型获取合适的上下文
        
        Args:
            task_type: 任务类型（tool_manager, search_assistant等）
        
        Returns:
            上下文字符串
        """
        return self.contexts.get(task_type, self.contexts["tool_manager"])
    
    async def handle_search_request(
        self,
        query: str,
        available_tools: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        处理搜索请求
        
        使用"search_assistant"上下文，调用所有search类工具，整理信息后返回。
        
        Args:
            query: 搜索查询
            available_tools: 可用的搜索工具列表
            context: 可选上下文（design_search Grant 由 Runtime 签发后经此传入）
        
        Returns:
            整理后的搜索结果
        """
        from llm_client import Message
        
        # 使用搜索助手上下文（注意：不要覆盖方法参数 context——它携带 design_search Grant id）
        system_context = self.get_context("search_assistant")
        
        # 准备工具信息
        tools_info = "\n".join([
            f"- {tool['tool_id']}: {tool.get('description', '无描述')}"
            for tool in available_tools if tool.get('category') == 'search'
        ])
        
        # 构建搜索请求（优先 super_search：真正的多引擎搜索；无则退回 web_fetcher）
        has_super_search = any(t.get("tool_id") == "super_search" for t in available_tools)
        search_tool_name = "super_search" if has_super_search else "web_fetcher"
        user_prompt = f"请帮我搜索以下信息：\n{query}\n\n请调用{search_tool_name}工具搜索信息。"
        
        system_prompt = f"{system_context}\n\n可用的搜索工具：\n{tools_info}"

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        
        # 定义可用的工具（只允许search类）
        if has_super_search:
            tools_for_llm = [{
                "type": "function",
                "function": {
                    "name": "super_search",
                    "description": "多引擎聚合网页搜索，返回标题/链接/摘要",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词"
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "返回结果条数，默认10"
                            }
                        },
                        "required": ["query"]
                    }
                }
            }]
        else:
            tools_for_llm = [{
                "type": "function",
                "function": {
                    "name": "web_fetcher",
                    "description": "获取网页内容",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "要访问的URL地址"
                            }
                        },
                        "required": ["url"]
                    }
                }
            }]
        
        # 调用LLM
        provider = self.model_config.get("primary", {}).get("provider", "newapi")
        model = self.model_config.get("primary", {}).get("model", "gpt-4")
        temperature = self.model_config.get("primary", {}).get("temperature", 0.7)
        
        try:
            response = await self.llm_client.chat(
                provider_name=provider,
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=4000,
                tools=tools_for_llm,
            )
            
            # 处理tool calls
            search_results = []
            max_iterations = 3
            iteration = 0
            
            while iteration < max_iterations:
                if not hasattr(response, 'tool_calls') or not response.tool_calls:
                    break
                
                iteration += 1
                logger.debug("工具库AI正在搜索 (第{}次)", iteration)
                
                for tool_call in response.tool_calls:
                    func_name = tool_call['function']['name']
                    
                    if func_name == 'super_search':
                        try:
                            args = json.loads(tool_call['function']['arguments'])
                            logger.debug("搜索: {}", args.get('query', ''))

                            # 授权化搜索（设计稿 §23.3）：super_search 与 web_fetcher
                            # 同样受 design_search Grant 约束，拒绝不触达网络。
                            result = await self._authorized_search(
                                "super_search",
                                "search",
                                {
                                    'query': args.get('query', ''),
                                    'max_results': args.get('max_results', 10),
                                },
                                context,
                            )
                            search_results.append(result)

                            # 添加到消息历史
                            messages.append(Message(
                                role="tool",
                                content=json.dumps(result, ensure_ascii=False),
                                tool_call_id=tool_call.get('id')
                            ))
                        except Exception as e:
                            logger.warning("搜索失败: {}", e)
                            messages.append(Message(
                                role="tool",
                                content=json.dumps({"error": str(e)}, ensure_ascii=False),
                                tool_call_id=tool_call.get('id')
                            ))
                    elif func_name == 'web_fetcher':
                        try:
                            args = json.loads(tool_call['function']['arguments'])
                            url = args.get('url', '')
                            logger.debug("访问: {}", url)
                            
                            # 授权化抓取（设计稿 §23.3）：enforce 模式下
                            # 无 design_search Grant 或主机不在 allowlist →
                            # 结构化拒绝回喂 LLM，不触达网络。
                            result = await self._authorized_search("web_fetcher", "fetch_content", {"url": url}, context)
                            search_results.append(result)
                            
                            # 添加到消息历史
                            messages.append(Message(
                                role="tool",
                                content=json.dumps(result, ensure_ascii=False),
                                tool_call_id=tool_call.get('id')
                            ))
                        except Exception as e:
                            logger.warning("搜索失败: {}", e)
                            messages.append(Message(
                                role="tool",
                                content=json.dumps({"error": str(e)}, ensure_ascii=False),
                                tool_call_id=tool_call.get('id')
                            ))
                
                # 继续对话
                response = await self.llm_client.chat(
                    provider_name=provider,
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=4000,
                    tools=tools_for_llm,
                )
            
            return {
                "success": True,
                "content": response.content,
                "raw_results": search_results,
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"搜索失败: {e}"
            }


    async def _authorized_search(
        self,
        tool_id: str,
        instruction: str,
        inputs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """授权化搜索调用（设计稿 §23.3）。

        - enforce 模式：无 Grant / 主机不在 allowlist / 预算耗尽 →
          结构化拒绝，不触达网络；每次调用（含拒绝）写审计。
        - enforce=false 或未接授权管理器 → authorize 内部直接放行，
          保持旧行为（兼容）。
        """
        registry = self.registry
        manager = getattr(registry, "authorization_manager", None) if registry is not None else None
        database = getattr(registry, "database", None) if registry is not None else None
        grant_id = context.get("grant_id") if isinstance(context, dict) else None

        if manager is not None:
            decision = manager.authorize(grant_id, tool_id, instruction, inputs)
            self._audit(database, tool_id, instruction, inputs, decision.allowed, decision.reason if not decision.allowed else None, grant_id)
            if not decision.allowed:
                return {
                    "success": False,
                    "blocked": True,
                    "error": f"search_denied: {decision.reason}",
                    "message": f"搜索未获授权: {decision.message}（可继续生成设计书，但无法联网检索）",
                    "inputs": inputs,
                }

        if tool_id == "super_search":
            from .super_search import SuperSearch
            result = await asyncio.to_thread(SuperSearch.execute, instruction, inputs)
        else:
            from .web_fetcher import WebFetcher
            result = await asyncio.to_thread(WebFetcher.execute, instruction, inputs)
        return result

    async def _authorized_fetch(self, url: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """兼容包装：web_fetcher 授权化抓取。"""
        return await self._authorized_search("web_fetcher", "fetch_content", {"url": url}, context)

    def _audit(self, database, tool_id: str, instruction: str, inputs: Dict[str, Any], success: bool, block_reason: Optional[str], grant_id: Optional[str]) -> None:
        if database is None:
            return
        try:
            database.save_audit_log(
                tool_id=tool_id,
                instruction=f"design_search:{instruction}",
                inputs=inputs,
                success=success,
                blocked=block_reason is not None,
                block_reason=block_reason,
                actor="designer_search",
                grant_id=grant_id,
            )
        except Exception:
            pass


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self, llm_client=None, model_config: Optional[Dict[str, Any]] = None):
        """
        初始化工具注册表
        
        Args:
            llm_client: LLM客户端（可选，用于AI agent）
            model_config: 模型配置（可选，用于AI agent）
        """
        self.tools = {}
        self.aliases = {}  # 别名 -> 主 ID（兼容旧数据中的历史工具名）
        self._register_default_tools()
        # 运行时注入（server lifespan）：设计期搜索的授权与审计（§23.3）。
        self.authorization_manager = None
        self.database = None
        
        # 初始化AI agent（如果提供了llm_client）
        self.agent = None
        if llm_client and model_config:
            self.agent = ToolRegistryAgent(llm_client, model_config, registry=self)
    
    def _register_default_tools(self):
        """注册默认工具"""
        self.register_tool("file_creator", FileCreator)
        self.register_tool("command_runner", CommandRunner)
        self.register_tool("package_installer", PackageInstaller)
        # manual_tool.py 的注册 ID 统一为 manual_tool；保留 "manual" 别名
        # 兼容旧数据（数据库已有计划的 tool 字段存的是 "manual:..."）
        self.register_tool("manual_tool", ManualTool, aliases=["manual"])
        self.register_tool("git_operator", GitOperator)
        self.register_tool("text_editor", TextEditor)
        self.register_tool("web_fetcher", WebFetcher)
        self.register_tool("super_search", SuperSearch)
        self.register_tool("code_context", CodeContext)
                        # 测试运行：白名单模板执行器（设计稿 §23.2，补全 test 位）
        self.register_tool("test_runner", TestRunner)
        # 语法门禁工具化：独立 lint 步骤（lint_file），exit_code 供确定性验收
        self.register_tool("lint_gate", LintGateTool)
        # GitHub 操作：gh CLI 白名单动作（PR 查询/创建），对外副作用不进计划白名单
        self.register_tool("github_operator", GitHubOperator)
        # HTTP API 调用：方法白名单 + SSRF 防护，对外副作用不进计划白名单
        self.register_tool("http_api", HttpApi)
        # 页面检查：Playwright 无头浏览器一次性断言+截图（懒加载依赖，缺依赖 blocked）
        self.register_tool("browser_check", BrowserCheck)
        # 项目工作记忆：执行状态持久化到工作区，让后续任务"接着干"（用户定位：只服务完成项目）
        self.register_tool("project_memory", ProjectMemory)
        # 外部工具接入：扫描 EAS_TOOLS_DIR（或默认兄弟目录 ../tools/harness_tools/tools），
        # 让外部工具仓库（get_metadata/execute 契约）能被实例化；目录缺失时为空操作。
        self.load_tools_from_dir(self._external_tools_dir())

    @staticmethod
    def _external_tools_dir() -> str:
        """解析外部工具目录。

        优先级：环境变量 EAS_TOOLS_DIR → 默认兄弟仓库 <harness根>/../tools/harness_tools/tools。
        环境变量显式指定则无条件采用；未设置时，默认目录仅在确实存在时返回，否则返回空串
        （load_tools_from_dir 对空串/不存在路径视为 0 个工具，harness 行为不变）。
        """
        explicit = os.environ.get("EAS_TOOLS_DIR")
        if explicit:
            return explicit
        default = (
            Path(__file__).resolve()                         # harness/python/tools/__init__.py
            .parent.parent.parent                            # → harness
            .parent / "tools" / "harness_tools" / "tools"    # → 兄弟仓库的外部工具目录
        )
        return str(default) if default.is_dir() else ""

    def load_tools_from_dir(self, directory: str) -> int:
        """从外部目录加载工具类（静态 get_metadata + execute 契约）。

        目录内每个非 `_` 开头的 .py 模块视为一个工具模块；模块中"本模块定义的、
        具备 get_metadata 与 execute 静态可调用的类"即工具类，按其 metadata 的
        tool_id 注册。返回实际加载数量；单个模块失败不影响其余。
        """
        directory = Path(directory) if directory else None
        if directory is None or not directory.is_dir():
            logger.warning("外部工具目录不存在或未配置（{}），跳过外部工具", directory)
            return 0

        count = 0
        for py_path in sorted(directory.glob("*.py")):
            if py_path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(py_path.stem, py_path)
                if spec is None or spec.loader is None:
                    logger.warning("外部工具模块无法创建 spec: {}", py_path.name)
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                logger.warning("外部工具模块加载失败 {}: {}", py_path.name, exc)
                continue

            tool_class = next(
                (obj for _, obj in inspect.getmembers(module, inspect.isclass)
                 if self._is_external_tool_class(obj, module.__name__)),
                None,
            )
            if tool_class is None:
                logger.warning("外部工具模块内未找到工具类: {}", py_path.name)
                continue

            try:
                metadata = tool_class.get_metadata()
            except Exception as exc:  # noqa: BLE001
                logger.warning("外部工具 get_metadata 异常 {}: {}", py_path.name, exc)
                continue
            tool_id = metadata.get("tool_id")
            if not tool_id:
                logger.warning("外部工具 metadata 缺少 tool_id: {}", py_path.name)
                continue

            self.register_tool(tool_id, tool_class)
            count += 1
            logger.info("外部工具注册成功: {}", tool_id)
        return count

    @staticmethod
    def _is_external_tool_class(obj, module_name: str) -> bool:
        """是否为本模块内定义的、具备 get_metadata 与 execute 静态方法的工具类。"""
        return (
            inspect.isclass(obj)
            and getattr(obj, "__module__", None) == module_name
            and callable(getattr(obj, "get_metadata", None))
            and callable(getattr(obj, "execute", None))
        )

    def register_tool(self, tool_id: str, tool_class, aliases: List[str] = None):
        """注册工具。aliases 中的旧名可继续被 get_tool/execute_tool 解析。"""
        self.tools[tool_id] = tool_class
        for alias in aliases or []:
            self.aliases[alias] = tool_id
    
    def get_tool(self, tool_id: str):
        """获取工具（先查主名，再查别名）"""
        tool = self.tools.get(tool_id)
        if tool is None:
            canonical = self.aliases.get(tool_id)
            if canonical:
                tool = self.tools.get(canonical)
        return tool
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有工具"""
        tools_list = []
        for tool_id, tool_class in self.tools.items():
            try:
                metadata = tool_class.get_metadata()
                tools_list.append(metadata)
            except:
                tools_list.append({
                    "tool_id": tool_id,
                    "name": tool_id,
                    "error": "无法获取元数据"
                })
        return tools_list
    
    def execute_tool(self, tool_id: str, instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具"""
        tool_class = self.get_tool(tool_id)
        if not tool_class:
            return {
                "success": False,
                "message": f"工具不存在: {tool_id}",
                "error": "Tool not found"
            }
        
        try:
            return tool_class.execute(instruction, inputs)
        except Exception as e:
            return {
                "success": False,
                "message": f"工具执行失败: {str(e)}",
                "error": str(e)
            }
    
    async def get_tool_info(self) -> List[Dict[str, Any]]:
        """
        获取工具信息（通过dispatcher调用）
        
        Returns:
            工具列表
        """
        return self.list_tools()
    
    async def handle_search_request(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        处理搜索请求（通过dispatcher调用）
        
        使用工具库AI agent来处理搜索请求。
        AI agent会使用"search_assistant"上下文，调用所有search类工具，整理信息后返回。
        
        Args:
            query: 搜索查询
            context: 可选的上下文信息
        
        Returns:
            搜索结果
        """
        if not self.agent:
            return {
                "success": False,
                "error": "工具库AI agent未初始化",
                "message": "请先初始化LLM客户端"
            }
        
        # 获取所有工具信息
        available_tools = self.list_tools()
        
        # 使用AI agent处理搜索请求（context 携带 design_search Grant id）
        return await self.agent.handle_search_request(query, available_tools, context)


# 全局工具注册表实例（初始化时不带AI agent）
_registry = None


def get_registry() -> ToolRegistry:
    """获取全局工具注册表"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def init_registry_with_ai(llm_client, model_config: Dict[str, Any]) -> ToolRegistry:
    """
    初始化带AI agent的工具注册表
    
    Args:
        llm_client: LLM客户端
        model_config: 模型配置
    
    Returns:
        工具注册表实例
    """
    global _registry
    _registry = ToolRegistry(llm_client, model_config)
    return _registry
