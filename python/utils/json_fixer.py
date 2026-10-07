"""
JSON 修复工具
用于修复 AI 返回的不规范 JSON
"""

import json
import re
from typing import Any, Dict, Optional, Tuple


class JSONFixer:
    """JSON 修复器"""
    
    @staticmethod
    def fix_json(text: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        尝试修复损坏的 JSON
        
        返回：(修复后的dict, 错误信息)
        """
        if not text:
            return None, "空文本"
        
        # 1. 尝试直接解析
        try:
            return json.loads(text), None
        except json.JSONDecodeError as e:
            original_error = str(e)
        
        # 2. 提取 JSON 代码块
        text = JSONFixer._extract_json_block(text)
        
        # 3. 尝试再次解析
        try:
            return json.loads(text), None
        except json.JSONDecodeError:
            pass
        
        # 4. 修复常见问题
        fixed_text = text
        
        # 修复未闭合的字符串
        fixed_text = JSONFixer._fix_unclosed_strings(fixed_text)
        
        # 修复尾部逗号
        fixed_text = JSONFixer._fix_trailing_commas(fixed_text)
        
        # 修复缺失的引号
        fixed_text = JSONFixer._fix_unquoted_keys(fixed_text)
        
        # 修复单引号
        fixed_text = JSONFixer._fix_single_quotes(fixed_text)
        
        # 5. 再次尝试解析
        try:
            return json.loads(fixed_text), None
        except json.JSONDecodeError:
            pass
        
        # 6. 尝试修复未闭合的括号
        fixed_text = JSONFixer._fix_unclosed_brackets(fixed_text)
        
        # 7. 最后一次尝试
        try:
            return json.loads(fixed_text), None
        except json.JSONDecodeError as e:
            return None, f"无法修复: {original_error}"
    
    @staticmethod
    def _extract_json_block(text: str) -> str:
        """提取 JSON 代码块"""
        # 移除 markdown 代码块标记
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        
        # 查找第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]
        
        return text.strip()
    
    @staticmethod
    def _fix_unclosed_strings(text: str) -> str:
        """修复未闭合的字符串"""
        # 查找最后一个未闭合的引号
        lines = text.split('\n')
        fixed_lines = []
        
        for line in lines:
            # 统计引号数量
            quote_count = line.count('"') - line.count('\\"')
            
            # 如果引号数量是奇数，说明有未闭合的
            if quote_count % 2 == 1:
                # 在行尾添加引号
                if line.rstrip().endswith(','):
                    line = line.rstrip()[:-1] + '",'
                elif line.rstrip().endswith('}') or line.rstrip().endswith(']'):
                    line = line.rstrip()
                    line = line[:-1] + '"' + line[-1]
                else:
                    line = line.rstrip() + '"'
            
            fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    @staticmethod
    def _fix_trailing_commas(text: str) -> str:
        """移除尾部逗号"""
        # 移除 } 前的逗号
        text = re.sub(r',(\s*})', r'\1', text)
        # 移除 ] 前的逗号
        text = re.sub(r',(\s*])', r'\1', text)
        return text
    
    @staticmethod
    def _fix_unquoted_keys(text: str) -> str:
        """修复未加引号的键"""
        # 匹配未加引号的键（简单情况）
        text = re.sub(r'([{,]\s*)(\w+)(\s*:)', r'\1"\2"\3', text)
        return text
    
    @staticmethod
    def _fix_single_quotes(text: str) -> str:
        """将单引号替换为双引号"""
        # 简单替换（可能不完美，但能处理大部分情况）
        text = text.replace("'", '"')
        return text
    
    @staticmethod
    def _fix_unclosed_brackets(text: str) -> str:
        """修复未闭合的括号"""
        # 只统计 JSON 结构括号；字符串值中的括号不是结构的一部分。
        # 例如截断响应 {"message": "use { safely", 会被朴素 count()
        # 误判为缺两个 }，从而把原本可恢复的 JSON 再次破坏。
        stack = []
        in_string = False
        escaped = False
        for char in text:
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in '{[':
                stack.append(char)
            elif char in '}]' and stack:
                expected = '{' if char == '}' else '['
                if stack[-1] == expected:
                    stack.pop()
        
        # 移除末尾的逗号和空白
        text = text.rstrip().rstrip(',')
        
        # 按实际嵌套顺序补齐缺失的闭合括号。
        text += ''.join('}' if char == '{' else ']' for char in reversed(stack))
        
        return text
    
    @staticmethod
    def validate_plan_structure(data: dict) -> Tuple[bool, Optional[str]]:
        """
        验证执行计划的结构
        
        返回：(是否有效, 错误信息)
        """
        # 检查必需字段
        if "segments" not in data:
            return False, "缺少 segments 字段"
        
        if not isinstance(data["segments"], list):
            return False, "segments 必须是数组"
        
        if len(data["segments"]) == 0:
            return False, "segments 不能为空"
        
        # 检查每个 segment
        for i, segment in enumerate(data["segments"]):
            if not isinstance(segment, dict):
                return False, f"segment[{i}] 必须是对象"
            
            if "description" not in segment:
                return False, f"segment[{i}] 缺少 description"
            
            if "steps" not in segment:
                return False, f"segment[{i}] 缺少 steps"
            
            if not isinstance(segment["steps"], list):
                return False, f"segment[{i}].steps 必须是数组"
            
            # 检查每个 step
            for j, step in enumerate(segment["steps"]):
                if not isinstance(step, dict):
                    return False, f"segment[{i}].steps[{j}] 必须是对象"
                
                required_fields = ["description", "tool", "inputs"]
                for field in required_fields:
                    if field not in step:
                        return False, f"segment[{i}].steps[{j}] 缺少 {field}"
        
        return True, None


def fix_and_validate_plan_json(text: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    修复并验证执行计划 JSON
    
    返回：(修复后的dict, 错误信息)
    """
    # 1. 修复 JSON
    data, error = JSONFixer.fix_json(text)
    
    if data is None:
        return None, error
    
    # 2. 验证结构
    valid, error = JSONFixer.validate_plan_structure(data)
    
    if not valid:
        return None, error
    
    return data, None


# agent_tool 步骤的计划期契约（fail-closed）。
# 新增 agent 工具时在此登记契约；执行期边界仍由 Grant 独立强制。
AGENT_TOOL_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "coding_agent": {
        "required_inputs": ("path", "instruction", "allowed_tools"),
        "allowed_tools": {"text_editor", "test_runner"},
        "require_acceptance": True,
    },
}

# 普通工具轻契约（设计稿 §23.2）：不涉及 Grant 白名单，只校验必填输入与验收。
TOOL_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "test_runner": {
        "required_inputs": ("suite",),
        "require_acceptance": True,
        "required_acceptance_types": {"exit_code", "test_passes"},
    },
    "lint_gate": {
        "required_inputs": ("path",),
        "require_acceptance": True,
        "required_acceptance_types": {"exit_code"},
    },
    "browser_check": {
        "required_inputs": ("url",),
        "require_acceptance": True,
        "required_acceptance_types": {"exit_code"},
    },
}

ACCEPTANCE_TYPES = {"file_exists", "content_contains", "exit_code", "diff_scope", "test_passes"}


def validate_tool_contracts(data: dict) -> Tuple[bool, Optional[str]]:
    """普通工具轻契约校验（test_runner / lint_gate 等，§23.2）。

    缺必填输入 / acceptance 缺失 / acceptance 不含要求类型 → 计划打回。
    """
    for i, segment in enumerate(data.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        for j, step in enumerate(segment.get("steps") or []):
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool", ""))
            contract = TOOL_CONTRACTS.get(tool)
            if contract is None:
                continue
            label = f"segment[{i}].steps[{j}] ({tool})"
            inputs = step.get("inputs")
            if not isinstance(inputs, dict):
                return False, f"{label}: inputs 必须是对象"
            for field in contract["required_inputs"]:
                value = inputs.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    return False, f"{label}: 缺少 inputs.{field}"
            if contract.get("require_acceptance"):
                acceptance = step.get("acceptance")
                if not isinstance(acceptance, list) or len(acceptance) == 0:
                    return False, f"{label}: 必须携带非空 acceptance（确定性验收）"
                required_types = contract.get("required_acceptance_types") or set()
                if required_types and not any(
                    isinstance(rule, dict) and str(rule.get("type", "")) in required_types
                    for rule in acceptance
                ):
                    return False, (
                        f"{label}: acceptance 必须包含 "
                        f"{sorted(required_types)} 类型规则"
                    )
    return True, None


def validate_agent_steps(data: dict) -> Tuple[bool, Optional[str]]:
    """计划期校验 agent_tool 步骤契约；普通步骤不受影响。

    设计期拒绝比执行期拒绝便宜：缺 path、allowed_tools 越权、
    无 acceptance 的 agent 步骤在这里直接打回重试，不进入执行。
    执行期的 Grant 资源/预算边界与此独立存在（双保险）。
    """
    for i, segment in enumerate(data.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        for j, step in enumerate(segment.get("steps") or []):
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool", ""))
            contract = AGENT_TOOL_CONTRACTS.get(tool)
            if contract is None:
                continue
            label = f"segment[{i}].steps[{j}] ({tool})"
            inputs = step.get("inputs")
            if not isinstance(inputs, dict):
                return False, f"{label}: inputs 必须是对象"
            for field in contract["required_inputs"]:
                value = inputs.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    return False, f"{label}: 缺少 inputs.{field}"
            allowed = inputs.get("allowed_tools")
            if (
                not isinstance(allowed, list)
                or not allowed
                or any(str(t) not in contract["allowed_tools"] for t in allowed)
            ):
                return False, (
                    f"{label}: inputs.allowed_tools 只能包含 "
                    f"{sorted(contract['allowed_tools'])}，收到: {allowed!r}"
                )
            if str(inputs.get("path", "")).endswith("/"):
                return False, f"{label}: path 必须是文件（不能是目录）"
            if contract.get("require_acceptance"):
                acceptance = step.get("acceptance")
                if not isinstance(acceptance, list) or len(acceptance) == 0:
                    return False, f"{label}: 必须携带非空 acceptance（确定性验收）"
                for rule in acceptance:
                    if not isinstance(rule, dict) or str(rule.get("type", "")) not in ACCEPTANCE_TYPES:
                        return False, f"{label}: acceptance 规则非法: {rule!r}"
                # 验收-指令一致性（治本：防"验收词凭空捏造"——真实首跑 step20/22 因此失败）。
                # content_contains 的 text 必须至少有一条与 instruction/description 有交集；
                # 零交集 = 模型不可能知道要产出该标识符，计划期直接打回。
                cc_texts = [
                    str(rule.get("text", "")).strip()
                    for rule in acceptance
                    if isinstance(rule, dict) and str(rule.get("type", "")) == "content_contains"
                ]
                cc_texts = [s for s in cc_texts if s]
                if cc_texts:
                    instruction = str(inputs.get("instruction", ""))
                    description = str(step.get("description", ""))
                    hay = f"{instruction} {description}".lower()

                    def _text_matches(s: str) -> bool:
                        # 宽松验收（规则 5）：def/class 前缀文本剥出标识符后匹配，
                        # "def load_knowledge" 只要求 instruction 里有 "load_knowledge"。
                        s_low = s.lower()
                        if s_low in hay:
                            return True
                        m = re.match(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", s_low)
                        if m:
                            return m.group(1) in hay
                        # token 级规则：转移式指令（"return 0 to 1"）↔ 期望产物
                        # （"return 1"）——文本的显著 token 全部出现在指令中即视为有交集。
                        toks = [t for t in re.findall(r"[A-Za-z_][\w]*", s_low) if len(t) >= 2]
                        if toks and all(tok in hay for tok in toks):
                            return True
                        return False

                    if not any(_text_matches(s) for s in cc_texts):
                        return False, (
                            f"{label}: 验收与指令零交集——content_contains 检查的 "
                            f"{cc_texts[:3]} 未在 instruction/description 中出现；"
                            "请在 instruction 中显式写出这些标识符，或改用指令中已有的标识符/宽松验收"
                        )
    return True, None


def validate_plan_tools(data: dict, allowed: set) -> Tuple[bool, Optional[str]]:
    """计划步骤的 tool 必须在允许集合内（注册表为准，禁止发明名字）。"""
    allowed_set = {str(item) for item in allowed}
    for i, segment in enumerate(data.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        for j, step in enumerate(segment.get("steps") or []):
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            label = f"segment[{i}].steps[{j}]"
            if not tool:
                return False, f"{label}: 缺少 tool"
            if tool not in allowed_set:
                preview = ", ".join(sorted(allowed_set)[:12])
                more = "…" if len(allowed_set) > 12 else ""
                return False, (
                    f"{label}: 未注册或不允许的工具 {tool!r}；"
                    f"允许: {preview}{more}"
                )
    return True, None
