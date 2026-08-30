import json
import os
from pathlib import Path
from typing import List, Dict, Any


class KnowledgeBase:
    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self._knowledge_data: List[Dict[str, Any]] = []
        self._load_knowledge_base()

    def _load_knowledge_base(self) -> None:
        """从指定路径加载知识库数据"""
        kb_file = Path(self.kb_path)
        
        if kb_file.exists() and kb_file.is_file():
            try:
                with open(kb_file, 'r', encoding='utf-8') as f:
                    if kb_file.suffix.lower() == '.json':
                        self._knowledge_data = json.load(f)
                        if not isinstance(self._knowledge_data, list):
                            self._knowledge_data = [self._knowledge_data]
                    else:
                        # 处理其他文本格式
                        self._knowledge_data = []
            except (json.JSONDecodeError, IOError):
                self._knowledge_data = []
        elif kb_file.is_dir():
            # 如果是目录，加载所有JSON文件
            self._load_from_directory(kb_file)
        else:
            self._knowledge_data = []

    def _load_from_directory(self, directory: Path) -> None:
        """从目录加载所有知识库文件"""
        self._knowledge_data = []
        try:
            for file_path in directory.glob('*.json'):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._knowledge_data.extend(data)
                    else:
                        self._knowledge_data.append(data)
        except (json.JSONDecodeError, IOError):
            pass

    def query(self, keyword: str) -> list:
        """查询相关知识条目
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配关键词的知识条目列表
        """
        if not keyword or not isinstance(keyword, str):
            return []
        
        keyword_lower = keyword.lower()
        results = []
        
        for item in self._knowledge_data:
            if self._matches_keyword(item, keyword_lower):
                results.append(item)
        
        return results

    def _matches_keyword(self, item: Dict[str, Any], keyword: str) -> bool:
        """检查条目是否匹配关键词
        
        Args:
            item: 知识条目
            keyword: 小写关键词
            
        Returns:
            是否匹配
        """
        if not isinstance(item, dict):
            return False
        
        # 在所有字符串值中搜索关键词
        for value in item.values():
            if isinstance(value, str):
                if keyword in value.lower():
                    return True
            elif isinstance(value, list):
                for sub_item in value:
                    if isinstance(sub_item, str) and keyword in sub_item.lower():
                        return True
        
        return False
