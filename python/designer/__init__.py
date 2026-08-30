"""
Designer AI - 设计师

职责：
1. Phase 1: 生成设计书
2. Phase 2: 生成执行计划
3. 评估问题严重程度
4. 生成总结报告
"""

from typing import Any, Dict, List

from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    Step,
    ToolAssessment,
    ToolRequirement,
)
from utils.interfaces import Designer
from llm_client import LLMClient, Message
from logging_config import get_logger

logger = get_logger(__name__)


class GenerationError(Exception):
    """LLM 生成失败异常：设计书/执行计划无法生成或解析。

    生成失败必须显式抛出，不得用占位符设计书（components=["待定"]）、
    原样返回旧设计或伪造的 Failed 占位计划来掩盖故障。
    """


class DefaultDesigner(Designer):
    """默认Designer实现"""
    
    def __init__(self, llm_client: LLMClient, model_config: Dict[str, Any], dispatcher=None) -> None:
        """
        初始化设计器
        
        Args:
            llm_client: LLM客户端
            model_config: 模型配置
            dispatcher: 调度器（用于访问工具库）
        """
        self.llm_client = llm_client
        self.model_config = model_config
        self.dispatcher = dispatcher
        
        # 初始化缓存
        from cache import DesignCache, PlanCache
        self.design_cache = DesignCache()
        self.plan_cache = PlanCache()
    
    @property
    def name(self) -> str:
        return "designer"
    
    @property
    def version(self) -> str:
        return "0.1.0"
    
    async def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """统一处理入口"""
        action = request.get("action")
        
        if action == "generate_design":
            design = await self.generate_design(request["user_request"])
            return {"design": design.model_dump()}
        elif action == "generate_plan":
            plan = await self.generate_plan(DesignDocument(**request["design"]))
            return {"plan": plan.model_dump()}
        elif action == "assess_severity":
            severity = await self.assess_severity(request["issue"], request.get("context", {}))
            return {"severity": severity}
        elif action == "generate_summary":
            summary = await self.generate_summary(
                ExecutionPlan(**request["plan"]),
                [Step(**s) for s in request["results"]],
            )
            return {"summary": summary}
        else:
            raise ValueError(f"Unknown action: {action}")
    
    async def health_check(self) -> bool:
        return True
    
    async def generate_design(self, user_request: str) -> DesignDocument:
        """
        Phase 1: 生成设计书
        
        通过dispatcher访问工具库获取工具信息。
        """
        
        # 检查缓存
        cached_design = self.design_cache.get(user_request)
        if cached_design:
            logger.info("使用缓存的设计书")
            return DesignDocument(**cached_design)
        
        # 通过dispatcher获取工具信息
        tools_info = ""
        if self.dispatcher:
            try:
                available_tools = await self.dispatcher.request_tool_info()
                tools_info_lines = []
                for tool in available_tools:
                    category = tool.get('category', 'other')
                    tools_info_lines.append(f"  - {tool['tool_id']} [{category}]: {tool.get('description', '无描述')}")
                tools_info = "\n".join(tools_info_lines)
            except Exception as e:
                logger.warning("获取工具信息失败: {}", e)
                tools_info = "（工具信息不可用）"
        else:
            tools_info = "（未配置dispatcher）"
        
        # 构建详细的prompt
        system_prompt = f"""你是一个资深的系统架构师和技术专家。

系统中当前可用的工具（仅供参考）：
{tools_info}

注意：这些工具在设计阶段不可执行，仅作为设计参考。你可以在设计书中提及需要使用哪些工具，但现在不要执行它们。

你的任务是根据用户需求，生成一份详细的技术设计书。

设计书必须包含两部分：

**Part 1: 架构设计**
1. architecture（架构）：
   - components: 列出所有主要组件（如：前端、后端、数据库、API等）
   - interactions: 描述组件之间如何交互
   
2. difficulty（难度评估）：
   - overall: 整体难度（easy/medium/hard）
   - breakdown: 各模块的具体难度，格式如 {{"前端": "medium", "后端": "hard"}}

**Part 2: 工具评估**
1. required_tools: 列出需要的工具，每个包含：
   - tool_id: 工具标识（如 "code-generator", "image-generator"）
   - availability: 可用性（available/unavailable/partial）
   - quality: 质量评估（high/medium/low）
   - limitations: 限制说明（可选）
   - impact: 对项目的影响（可选）
   
2. overall_tool_completeness: 工具完备度（0-1之间的数字）
3. risks: 潜在风险列表

请以JSON格式返回，严格遵循以下结构：
{{
  "architecture": {{
    "components": ["component1", "component2"],
    "interactions": ["interaction1", "interaction2"]
  }},
  "difficulty": {{
    "overall": "medium",
    "breakdown": {{"module1": "easy", "module2": "hard"}}
  }},
  "required_tools": [
    {{
      "tool_id": "tool-name",
      "availability": "available",
      "quality": "high",
      "limitations": "some limitation",
      "impact": "high impact"
    }}
  ],
  "overall_tool_completeness": 0.85,
  "risks": ["risk1", "risk2"]
}}

注意：
1. 分析要具体、实用
2. 组件要全面覆盖
3. 工具评估要实际
4. 只返回JSON，不要其他解释
"""
        
        user_content = f"""用户需求：
{user_request}

请生成详细的设计书。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_content),
        ]
        
        # 定义搜索工具（通过dispatcher调用）
        tools_for_llm = [{
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "搜索网络信息以辅助设计决策。可以获取技术文档、最佳实践、库信息等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询或要访问的URL"
                        }
                    },
                    "required": ["query"]
                }
            }
        }]
        
        # 调用LLM
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        temperature = self.model_config["primary"]["temperature"]
        
        try:
            response = await self.llm_client.chat(
                provider_name=provider,
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=8000,
                tools=tools_for_llm if self.dispatcher else None,  # 只有配置了dispatcher才启用搜索
            )
            
            # 处理tool calls（通过dispatcher）
            import json
            max_iterations = 5  # 防止无限循环
            iteration = 0
            
            logger.debug("===== 检查工具调用 =====")
            logger.debug("response 类型: {}", type(response))
            logger.debug("hasattr tool_calls: {}", hasattr(response, "tool_calls"))
            if hasattr(response, 'tool_calls'):
                logger.debug("tool_calls 值: {}", response.tool_calls)
            logger.debug("==================")
            
            while iteration < max_iterations and self.dispatcher:
                # 检查是否有tool calls
                if not hasattr(response, 'tool_calls') or not response.tool_calls:
                    break
                
                iteration += 1
                logger.info("设计器请求搜索信息（第{}次）", iteration)
                
                # 处理每个tool call
                for tool_call in response.tool_calls:
                    func_name = tool_call['function']['name']
                    
                    if func_name == 'web_search':
                        try:
                            # 解析参数
                            args = json.loads(tool_call['function']['arguments'])
                            query = args.get('query', '')
                            logger.debug("搜索: {}", query)
                            
                            # 通过dispatcher请求搜索
                            result = await self.dispatcher.request_search(query)
                            
                            # 添加工具结果到消息历史
                            tool_result_content = json.dumps(result, ensure_ascii=False)
                            messages.append(Message(
                                role="tool",
                                content=tool_result_content,
                                tool_call_id=tool_call.get('id')
                            ))
                            
                        except Exception as e:
                            logger.error("搜索失败: {}", e)
                            messages.append(Message(
                                role="tool",
                                content=json.dumps({"error": str(e)}, ensure_ascii=False),
                                tool_call_id=tool_call.get('id')
                            ))
                    else:
                        # 不允许的工具调用
                        logger.warning("拒绝调用工具: {}（设计阶段只允许搜索）", func_name)
                        messages.append(Message(
                            role="tool",
                            content=json.dumps({"error": "设计阶段只允许使用web_search"}, ensure_ascii=False),
                            tool_call_id=tool_call.get('id')
                        ))
                
                # 继续对话
                response = await self.llm_client.chat(
                    provider_name=provider,
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=8000,
                    tools=tools_for_llm,
                )
            
            # 如果进行了工具调用，需要明确要求返回JSON
            if iteration > 0:
                logger.info("工具调用完成，请求生成最终设计书...")
                messages.append(Message(
                    role="user",
                    content="现在你已经获得了足够的信息。请基于这些信息生成完整的设计书JSON。记住：只返回JSON格式的设计书，不要其他解释或总结。"
                ))
                response = await self.llm_client.chat(
                    provider_name=provider,
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=8000,
                    tools=None,  # 不再提供工具，确保返回JSON
                )
            
            # 解析JSON响应
            import json
            response_text = response.content.strip()
            
            # 调试日志
            logger.debug("===== LLM响应 =====")
            logger.debug("响应长度: {}", len(response_text))
            logger.debug("前200字符: {}", response_text[:200])
            logger.debug("==================")
            
            # 检查是否为JSON格式
            is_json_response = response_text.strip().startswith(('{', '[')) or '```json' in response_text or '```' in response_text
            
            # 如果不是JSON，可能是搜索结果或其他内容，需要再次请求
            if not is_json_response:
                logger.warning("LLM返回的不是JSON格式，再次请求生成设计书...")
                messages.append(Message(
                    role="assistant",
                    content=response_text
                ))
                messages.append(Message(
                    role="user",
                    content="请基于以上信息生成完整的设计书JSON。记住：只返回JSON格式的设计书，不要其他解释或总结。必须严格遵循之前给出的JSON结构。"
                ))
                response = await self.llm_client.chat(
                    provider_name=provider,
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=8000,
                    tools=None,  # 不再提供工具
                )
                response_text = response.content.strip()
                logger.debug("===== 第二次响应 =====")
                logger.debug("响应长度: {}", len(response_text))
                logger.debug("前200字符: {}", response_text[:200])
                logger.debug("==================")
            
            # 尝试提取JSON（可能被markdown包裹）
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            logger.debug("提取后的JSON长度: {}", len(response_text))
            logger.debug("提取后的JSON前200字符: {}", response_text[:200])
            
            design_data = json.loads(response_text)
            
            # 构建DesignDocument
            from uuid import uuid4
            from datetime import datetime
            
            design = DesignDocument(
                design_id=uuid4(),
                user_request=user_request,
                timestamp=datetime.utcnow(),
                part1_design=DesignPart1(
                    architecture=Architecture(
                        components=design_data["architecture"]["components"],
                        interactions=design_data["architecture"]["interactions"],
                    ),
                    difficulty=Difficulty(
                        overall=design_data["difficulty"]["overall"],
                        breakdown=design_data["difficulty"]["breakdown"],
                    ),
                ),
                part2_tool_assessment=ToolAssessment(
                    required_tools=[
                        ToolRequirement(**tool) for tool in design_data["required_tools"]
                    ],
                    overall_tool_completeness=design_data["overall_tool_completeness"],
                    risks=design_data["risks"],
                ),
            )
            
            # 保存到缓存
            self.design_cache.set(user_request, design.model_dump(mode="json"))
            
            return design
            
        except Exception as e:
            # 生成失败必须显式暴露：绝不再返回 components=["待定"] 的占位设计书
            # 让下游把残次品当正常设计继续审批、执行。
            logger.error("设计书生成失败: {}", e)
            logger.error("错误类型: {}", type(e).__name__)
            raise GenerationError(f"设计书生成失败: {e}") from e
    
    async def revise_design(self, original_design: DesignDocument, feedback: str) -> DesignDocument:
        """
        根据用户反馈修订设计书
        
        Args:
            original_design: 原始设计书
            feedback: 用户反馈
            
        Returns:
            修订后的设计书
        """
        logger.info("根据反馈修订设计书: {}", feedback)
        
        # 获取工具信息
        tools_info = ""
        if self.dispatcher:
            try:
                available_tools = await self.dispatcher.request_tool_info()
                tools_info_lines = []
                for tool in available_tools:
                    category = tool.get('category', 'other')
                    tools_info_lines.append(f"  - {tool['tool_id']} [{category}]: {tool.get('description', '无描述')}")
                tools_info = "\n".join(tools_info_lines)
            except Exception as e:
                logger.warning("获取工具信息失败: {}", e)
                tools_info = "（工具信息不可用）"
        else:
            tools_info = "（未配置dispatcher）"
        
        # 构建原始设计书的文本表示
        original_design_text = f"""
原始架构设计：
- 组件: {', '.join(original_design.part1_design.architecture.components)}
- 数据流: {', '.join(original_design.part1_design.architecture.interactions)}
- 难度: {original_design.part1_design.difficulty.overall}
- 难度细分: {original_design.part1_design.difficulty.breakdown}

原始工具评估：
- 所需工具: {[t.tool_id for t in original_design.part2_tool_assessment.required_tools]}
- 工具完备度: {original_design.part2_tool_assessment.overall_tool_completeness}
- 风险: {original_design.part2_tool_assessment.risks}
"""
        
        # 构建修订prompt
        system_prompt = f"""你是一个资深的系统架构师和技术专家。

系统中当前可用的工具（仅供参考）：
{tools_info}

你的任务是根据用户的反馈，修订已有的技术设计书。

用户的原始需求是: {original_design.user_request}

{original_design_text}

用户的反馈是: {feedback}

请根据反馈修改设计书，保持原有合理的部分，改进用户不满意的地方。

设计书必须包含两部分：

**Part 1: 架构设计**
1. architecture（架构）：
   - components: 列出所有主要组件（如：前端、后端、数据库、API等）
   - interactions: 描述组件之间如何交互
   
2. difficulty（难度评估）：
   - overall: 整体难度（easy/medium/hard）
   - breakdown: 各模块的具体难度，格式如 {{"前端": "medium", "后端": "hard"}}

**Part 2: 工具评估**
1. required_tools: 列出需要的工具，每个包含：
   - tool_id: 工具标识（如 "code-generator", "image-generator"）
   - availability: 可用性（available/unavailable/partial）
   - quality: 质量评估（high/medium/low）
   - limitations: 限制说明（可选）
   - impact: 对项目的影响（可选）
   
2. overall_tool_completeness: 工具完备度（0-1之间的数字）
3. risks: 潜在风险列表

请以JSON格式返回，严格遵循以下结构：
{{
  "architecture": {{
    "components": ["component1", "component2"],
    "interactions": ["interaction1", "interaction2"]
  }},
  "difficulty": {{
    "overall": "medium",
    "breakdown": {{"module1": "easy", "module2": "hard"}}
  }},
  "required_tools": [
    {{
      "tool_id": "tool-name",
      "availability": "available",
      "quality": "high",
      "limitations": "some limitation",
      "impact": "high impact"
    }}
  ],
  "overall_tool_completeness": 0.85,
  "risks": ["risk1", "risk2"]
}}

注意：
1. 针对用户反馈进行修改
2. 保持合理的原有设计
3. 只返回JSON，不要其他解释
"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content="请根据反馈修订设计书。"),
        ]
        
        # 调用LLM
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        temperature = self.model_config["primary"]["temperature"]
        
        try:
            response = await self.llm_client.chat(
                provider_name=provider,
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=8000,
            )
            
            # 提取JSON内容
            import re
            import json
            content = response.content
            
            # 尝试多种方式提取JSON
            json_match = re.search(r'```json\s*({.*?})\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            elif content.strip().startswith('{'):
                pass  # 已经是JSON
            else:
                json_match = re.search(r'{.*}', content, re.DOTALL)
                if json_match:
                    content = json_match.group(0)
            
            design_data = json.loads(content.strip())
            
            # 构建新的DesignDocument（保留原ID和timestamp）
            from datetime import datetime
            
            revised_design = DesignDocument(
                design_id=original_design.design_id,  # 保留原ID
                user_request=original_design.user_request,
                timestamp=datetime.utcnow(),  # 更新时间戳
                part1_design=DesignPart1(
                    architecture=Architecture(
                        components=design_data["architecture"]["components"],
                        interactions=design_data["architecture"]["interactions"],
                    ),
                    difficulty=Difficulty(
                        overall=design_data["difficulty"]["overall"],
                        breakdown=design_data["difficulty"]["breakdown"],
                    ),
                ),
                part2_tool_assessment=ToolAssessment(
                    required_tools=[
                        ToolRequirement(**tool) for tool in design_data["required_tools"]
                    ],
                    overall_tool_completeness=design_data["overall_tool_completeness"],
                    risks=design_data["risks"],
                ),
            )
            
            logger.info("设计书修订完成")
            # 反馈必须跟随修订后的设计书进入计划生成，否则用户在拒绝时
            # 给出的具体要求（如文件名、路径）会在下一环节丢失。
            revised_design.user_comments = feedback
            # 同 design_id 的旧计划缓存作废：反馈改变了计划应当遵守的约束
            self.plan_cache.remove(str(original_design.design_id))
            return revised_design
            
        except Exception as e:
            logger.error("设计书修订失败: {}", e)
            # 修订失败必须显式暴露：静默返回原设计书会让用户以为反馈已生效。
            raise GenerationError(f"设计书修订失败: {e}") from e
    
    async def generate_plan(self, design: DesignDocument) -> ExecutionPlan:
        """生成执行计划（带重试机制）"""
        
        # 检查缓存（使用 design_id 作为键）
        cache_key = str(design.design_id)
        cached_plan = self.plan_cache.get(cache_key)
        if cached_plan:
            logger.info("使用缓存的执行计划")
            return ExecutionPlan(**cached_plan)
        
        system_prompt = """你是一个执行计划专家。根据设计书生成详细的、可执行的步骤计划。

❗ 重要：必须返回合法的 JSON 格式，不要添加任何解释文字。

[NOTE] 要求的 JSON 结构（精简版）：

```json
{
  "segments": [
    {
      "description": "项目初始化",
      "steps": [
        {
          "description": "创建项目目录",
          "tool": "file_creator",
          "inputs": {"path": "./project", "content": ""},
          "acceptance": [{"type": "file_exists", "path": "./project", "kind": "directory"}]
        },
        {
          "description": "创建主程序",
          "tool": "file_creator",
          "inputs": {"path": "./project/main.py", "content": "print('hello')"},
          "acceptance": [
            {"type": "file_exists", "path": "./project/main.py", "kind": "file"},
            {"type": "content_contains", "path": "./project/main.py", "text": "print('hello')"}
          ]
        }
      ]
    }
  ],
  "implementation_list": ["src/main.py", "README.md"]
}
```

[OK] JSON 规则：
1. 所有字符串用双引号 ""
2. 最后一个元素后不要有逗号
3. inputs 必须使用键 `path` 和 `content`，不要用 `file_path` / `file_content`
4. 每个能自动验证的步骤必须返回 `acceptance` 数组；支持 `file_exists`、`content_contains`、`exit_code`、`diff_scope`
5. 无法自动验证的步骤返回 `acceptance: []`，并在 `check_criteria` 中说明需要人工检查
6. 确保所有括号都正确闭合

📁 路径语义（重要）：
- 用户需求中明确给出的路径（无论绝对还是相对）必须**原样使用**，不得改写
- 用户未指定路径时，使用相对路径（如 "output/report.txt"），
  相对路径将自动解析到工作区根目录
- 目录路径以 / 结尾（如 "output/"），文件路径带扩展名

⚠️ 约束（重要）：
- 允许使用的工具：file_creator（创建新文件/目录）与 coding_agent（修改已存在的代码文件）。不要执行任何 shell 命令，不要使用 command_runner / package_installer / git_operator / text_editor / web_fetcher。
- 创建新文件用 file_creator；修改已存在文件的内容用 coding_agent（它能先读原文再做受控修改）。

🔧 coding_agent 步骤契约（必须全部满足，否则计划会被拒绝）：
1. inputs 必须包含：path（要修改的单个文件，相对路径）、instruction（一句话修改指令）、allowed_tools 固定为 ["text_editor"]
2. 一个 coding_agent 步骤只修改一个文件；多个文件拆成多个步骤
3. 必须携带非空 acceptance（content_contains / file_exists），不依赖人工检查
4. 示例：
   {"description": "修改 greet 返回值", "tool": "coding_agent", "inputs": {"path": "src/app.py", "instruction": "把 return 1 改成 return 'hello, world'", "allowed_tools": ["text_editor"]}, "acceptance": [{"type": "content_contains", "path": "src/app.py", "text": "hello, world"}]}

🎯 注意事项（重要）：
- description 要简洁（10-20字），不要冗余
- 不要在 description 中重复 inputs 的信息
- 去掉"用于"、"包括"等冗余词汇
- 每个步骤必须指定 tool
- 按照逻辑顺序组织步骤

[WARN] 只返回 JSON，不要添加任何解释文字或 markdown 标记！
[WARN] 保持简洁，避免冗长的描述！
"""
        
        user_content = f"""设计书：

**用户需求：**
{design.user_request}

**架构组件：**
{', '.join(design.part1_design.architecture.components)}

**架构交互：**
{', '.join(design.part1_design.architecture.interactions)}

**难度评估：**
{design.part1_design.difficulty.overall}

**所需工具：**
{[tool.tool_id for tool in design.part2_tool_assessment.required_tools]}
{f'''

**用户审批反馈（必须遵守）：**
{design.user_comments}''' if getattr(design, 'user_comments', None) else ''}

请生成详细的执行计划。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_content),
        ]
        
        # 重试机制：最多尝试 4 次，使用多层 fallback
        max_retries = 4
        last_error = None
        agent_contract_error = None
        
        # 定义 fallback 策略（按优先级）
        fallback_strategy = [
            {"type": "primary", "provider": self.model_config["primary"]["provider"], 
             "model": self.model_config["primary"]["model"], 
             "temperature": self.model_config["primary"]["temperature"]},
        ]
        
        # 添加配置的 fallback
        if "fallback" in self.model_config:
            fallback_strategy.append({
                "type": "fallback", 
                "provider": self.model_config["fallback"]["provider"], 
                "model": self.model_config["fallback"]["model"], 
                "temperature": self.model_config["fallback"]["temperature"]
            })
        
        # 添加额外的通用 fallback（如果还有空间）
        if len(fallback_strategy) < max_retries:
            # 尝试 stepfun 作为第三选择
            fallback_strategy.append({
                "type": "fallback-2", 
                "provider": "stepfun", 
                "model": "step-3.7-flash", 
                "temperature": 0.3
            })
        
        if len(fallback_strategy) < max_retries:
            # 尝试 hejuapi 作为第四选择
            fallback_strategy.append({
                "type": "fallback-3", 
                "provider": "hejuapi", 
                "model": "gpt-5.6-sol", 
                "temperature": 0.3
            })
        
        for attempt in range(max_retries):
            try:
                # 选择当前尝试使用的 API
                if attempt < len(fallback_strategy):
                    config = fallback_strategy[attempt]
                else:
                    # 如果超出策略数量，重复使用最后一个
                    config = fallback_strategy[-1]
                
                provider = config["provider"]
                model = config["model"]
                temperature = config["temperature"]
                api_type = config["type"]
                
                if attempt > 0:
                    logger.warning("重试生成执行计划...（尝试 {}/{}，使用 {}: {}/{}）", attempt + 1, max_retries, api_type, provider, model)
                    # 添加延迟，避免触发速率限制
                    import asyncio
                    wait_time = 2 * attempt  # 2秒, 4秒, 6秒
                    logger.info("等待 {} 秒后重试...", wait_time)
                    await asyncio.sleep(wait_time)
                else:
                    logger.debug("使用 {}: {}/{}", api_type, provider, model)
                
                response = await self.llm_client.chat(
                    provider_name=provider,
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=4000,  # 限制响应长度，提高稳定性和速度
                )
                
                # 使用 JSON 修复工具
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from utils.json_fixer import fix_and_validate_plan_json
                
                response_text = response.content.strip()
                
                # 打印响应长度，便于调试
                logger.debug("API 响应长度: {} 字符", len(response_text))
                if len(response_text) == 0:
                    last_error = "API 返回空响应"
                    logger.error("{}", last_error)
                    continue  # 重试
                
                # 尝试修复并验证 JSON
                plan_data, error = fix_and_validate_plan_json(response_text)
                
                if plan_data is None:
                    last_error = error
                    logger.error("JSON 解析/修复失败: {}", error)
                    # 如果响应很短，打印出来帮助调试
                    if len(response_text) < 200:
                        logger.debug("API 返回内容: {}", response_text)
                    continue  # 重试
                
                # agent_tool 步骤契约校验（fail-closed）：
                # 缺 path / allowed_tools 越权 / 无 acceptance 的 agent 步骤
                # 在设计期直接打回重试，不进入执行期。
                from utils.json_fixer import validate_agent_steps
                agent_ok, agent_error = validate_agent_steps(plan_data)
                if not agent_ok:
                    last_error = f"agent 步骤契约校验失败: {agent_error}"
                    agent_contract_error = agent_error
                    logger.error("{}", last_error)
                    continue  # 重试
                
                # 成功解析，构建 ExecutionPlan
                from uuid import uuid4
                from datetime import datetime
                
                segments = []
                for seg_data in plan_data["segments"]:
                    steps = []
                    for step_data in seg_data["steps"]:
                        # 规范化outputs字段：确保是字典或None
                        outputs_value = step_data.get("outputs")
                        if outputs_value is not None:
                            if isinstance(outputs_value, str):
                                outputs_value = {"value": outputs_value}
                            elif not isinstance(outputs_value, dict):
                                outputs_value = None
                        
                        # 规范化inputs字段：确保是字典
                        inputs_value = step_data.get("inputs", {})
                        if not isinstance(inputs_value, dict):
                            inputs_value = {"value": str(inputs_value)}
                        
                        raw_acceptance = step_data.get("acceptance", [])
                        if not isinstance(raw_acceptance, list):
                            raw_acceptance = []
                        acceptance = [item for item in raw_acceptance if isinstance(item, dict)]
                        check_criteria = step_data.get("check_criteria") or (
                            "执行成功" if acceptance else "手动检查"
                        )
                        
                        steps.append(Step(
                            step_id=uuid4(),
                            order=len(steps) + 1,  # 自动生成序号
                            description=step_data.get("description", "未知步骤"),
                            tool=step_data.get("tool", "manual_tool"),
                            inputs=inputs_value,
                            check_criteria=check_criteria,
                            acceptance=acceptance,
                            outputs=None,  # 使用默认值
                            status="Pending",
                        ))
                    
                    segments.append(PlanSegment(
                        segment_id=uuid4(),
                        description=seg_data.get("description", "未知阶段"),
                        steps=steps,
                    ))
                
                logger.info("执行计划生成成功（尝试 {}）", attempt + 1)
                
                plan = ExecutionPlan(
                    plan_id=uuid4(),
                    design_id=design.design_id,
                    created_at=datetime.utcnow(),
                    status="Pending",
                    segments=segments,
                    implementation_list=plan_data.get("implementation_list", []),
                )
                
                # 保存到缓存
                cache_key = str(design.design_id)
                self.plan_cache.set(cache_key, plan.model_dump(mode="json"))
                
                return plan
                
            except Exception as e:
                last_error = str(e)
                logger.error("生成执行计划时出错: {}", e)
                # 打印详细的异常类型
                logger.error("异常类型: {}", type(e).__name__)
                if attempt < max_retries - 1:
                    continue  # 重试
        
        # 所有重试都失败：显式失败，绝不返回伪造的 Failed 占位计划
        logger.error("执行计划生成失败，已重试 {} 次", max_retries)
        logger.error("最后错误: {}", last_error)

        # 重试换 provider 会稀释根因；agent 契约错误必须随异常暴露，
        # 否则用户只看到“Provider not found”而不知道计划到底违反了什么。
        agent_suffix = (
            f"（期间 agent 契约校验失败: {agent_contract_error}）"
            if agent_contract_error
            else ""
        )

        raise GenerationError(
            f"执行计划生成失败（已重试 {max_retries} 次）: {last_error}{agent_suffix}"
        )
    
    async def assess_severity(self, issue: str, context: Dict[str, Any]) -> int:
        """评估问题严重程度（1-4级）"""
        
        system_prompt = """你是一个问题评估专家。
评估问题的严重程度，返回1-4的数字：
- Level 1: 轻微问题，Executor自行处理
- Level 2: 中等问题，Designer插入修复步骤
- Level 3: 严重问题，需要用户选择方案
- Level 4: 致命问题，必须用户介入

只返回数字。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"问题：{issue}\n上下文：{context}"),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.1,
        )
        
        # TODO: 解析响应
        return 2  # 占位符
    
    async def generate_summary(self, plan: ExecutionPlan, results: List[Step]) -> str:
        """生成总结报告"""
        
        system_prompt = """你是一个总结专家。
根据执行计划和结果，生成简洁的总结报告。"""
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(
                role="user",
                content=f"计划：{plan.model_dump_json()}\n结果：{[r.model_dump() for r in results]}",
            ),
        ]
        
        provider = self.model_config["primary"]["provider"]
        model = self.model_config["primary"]["model"]
        
        response = await self.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.3,
        )
        
        return response.content
