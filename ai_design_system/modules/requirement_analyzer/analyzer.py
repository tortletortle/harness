import re
from typing import Dict, List


class RequirementAnalyzer:
    def parse(self, requirement: str) -> dict:
        """解析用户需求，返回结构化需求对象
        
        Args:
            requirement: 用户输入的需求文本
            
        Returns:
            包含以下字段的字典：
            - requirement_type: 需求类型 (功能需求/非功能需求/业务需求)
            - core_features: 核心功能点列表
            - constraints: 约束条件列表
        """
        requirement = requirement.strip()
        
        # 提取需求类型
        requirement_type = self._extract_requirement_type(requirement)
        
        # 提取核心功能点
        core_features = self._extract_core_features(requirement)
        
        # 提取约束条件
        constraints = self._extract_constraints(requirement)
        
        return {
            "requirement_type": requirement_type,
            "core_features": core_features,
            "constraints": constraints
        }
    
    def _extract_requirement_type(self, requirement: str) -> str:
        """识别需求类型"""
        requirement_lower = requirement.lower()
        
        # 非功能需求关键词
        nonfunctional_keywords = ['性能', '安全', '可靠', '可用性', '扩展', '兼容', '响应', '延迟', '吞吐', '并发']
        if any(keyword in requirement_lower for keyword in nonfunctional_keywords):
            return "非功能需求"
        
        # 业务需求关键词
        business_keywords = ['商业', '成本', '收益', '市场', '竞争', '用户体验', '交互']
        if any(keyword in requirement_lower for keyword in business_keywords):
            return "业务需求"
        
        # 默认为功能需求
        return "功能需求"
    
    def _extract_core_features(self, requirement: str) -> List[str]:
        """提取核心功能点"""
        features = []
        
        # 按常见功能关键词分割
        feature_keywords = ['实现', '支持', '提供', '具备', '包含', '拥有', '能够', '可以']
        
        # 按句号、逗号、分号分割
        sentences = re.split(r'[。，、；；]', requirement)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # 检查是否包含功能关键词
            for keyword in feature_keywords:
                if keyword in sentence:
                    # 提取功能描述
                    parts = sentence.split(keyword)
                    if len(parts) > 1:
                        feature = parts[1].strip()
                        # 移除括号内的内容
                        feature = re.sub(r'[（()\[\]【】].*?[）)\]\]】]', '', feature)
                        if feature and feature not in features:
                            features.append(feature)
                    break
            
            # 如果没有找到关键词，将整个句子作为功能点
            if not any(keyword in sentence for keyword in feature_keywords) and sentence and sentence not in features:
                features.append(sentence)
        
        return features if features else [requirement]
    
    def _extract_constraints(self, requirement: str) -> List[str]:
        """提取约束条件"""
        constraints = []
        
        # 约束关键词
        constraint_keywords = ['必须', '应该', '需要', '要求', '限制', '不能', '不允许', '只能', '仅', '且', '而且']
        constraint_indicators = ['在', '以', '低于', '高于', '少于', '多于', '范围', '时间', '期限', '期间']
        
        # 按句号、逗号、分号分割
        sentences = re.split(r'[。，、；；]', requirement)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # 检查是否包含约束关键词
            has_constraint_keyword = any(keyword in sentence for keyword in constraint_keywords)
            has_constraint_indicator = any(indicator in sentence for indicator in constraint_indicators)
            
            if (has_constraint_keyword or has_constraint_indicator) and sentence not in constraints:
                constraints.append(sentence)
        
        return constraints
