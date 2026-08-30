import hashlib
import json
from typing import Dict, List, Any


class AICoreEngine:
    def __init__(self, knowledge_base):
        self.knowledge_base = knowledge_base

    def process(self, requirement: dict) -> dict:
        """智能处理需求，返回特征向量
        
        Args:
            requirement: 包含需求信息的字典，例如 {"query": "...", "context": "..."}
            
        Returns:
            包含特征向量和相关知识的字典，格式:
            {
                "feature_vector": [...],
                "related_knowledge": [...],
                "requirement": requirement
            }
        """
        # 从知识库查询相关知识
        query_text = requirement.get("query", "")
        related_knowledge = self.knowledge_base.query(query_text)
        
        # 生成特征向量
        feature_vector = self._generate_feature_vector(requirement, related_knowledge)
        
        # 返回结果字典
        return {
            "feature_vector": feature_vector,
            "related_knowledge": related_knowledge,
            "requirement": requirement
        }
    
    def _generate_feature_vector(self, requirement: dict, related_knowledge: List[Any]) -> List[float]:
        """根据需求和相关知识生成特征向量
        
        Args:
            requirement: 需求字典
            related_knowledge: 相关知识列表
            
        Returns:
            特征向量列表
        """
        # 将需求和知识转换为字符串
        requirement_str = json.dumps(requirement, ensure_ascii=False, sort_keys=True)
        knowledge_str = json.dumps(related_knowledge, ensure_ascii=False, default=str)
        
        # 组合文本
        combined_text = requirement_str + knowledge_str
        
        # 使用哈希值生成确定性特征向量
        hash_obj = hashlib.md5(combined_text.encode('utf-8'))
        hash_hex = hash_obj.hexdigest()
        
        # 将哈希转换为特征向量（浮点数列表）
        feature_vector = [float(int(hash_hex[i:i+2], 16)) / 255.0 for i in range(0, len(hash_hex), 2)]
        
        return feature_vector
