"""
JSON 修复工具
用于修复 AI 返回的不规范 JSON
"""

import json
import re
from typing import Optional, Tuple


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
        # 统计括号
        open_braces = text.count('{')
        close_braces = text.count('}')
        open_brackets = text.count('[')
        close_brackets = text.count(']')
        
        # 移除末尾的逗号和空白
        text = text.rstrip().rstrip(',')
        
        # 添加缺失的闭合括号
        if open_brackets > close_brackets:
            text += ']' * (open_brackets - close_brackets)
        
        if open_braces > close_braces:
            text += '}' * (open_braces - close_braces)
        
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
