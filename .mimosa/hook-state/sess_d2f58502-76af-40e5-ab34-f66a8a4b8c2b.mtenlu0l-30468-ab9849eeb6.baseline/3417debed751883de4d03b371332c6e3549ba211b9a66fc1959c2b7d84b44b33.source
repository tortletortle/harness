"""
调度器（Dispatcher）

职责：
1. 作为设计器和工具库之间的中间层
2. 转发所有请求到工具库
3. 管理请求和响应的路由
"""

from typing import Any, Dict, List, Optional
from tools import ToolRegistry


class Dispatcher:
    """调度器 - 负责转发请求到工具库"""
    
    def __init__(self, tool_registry: ToolRegistry):
        """
        初始化调度器
        
        Args:
            tool_registry: 工具注册表实例
        """
        self.tool_registry = tool_registry
    
    async def request_tool_info(self) -> List[Dict[str, Any]]:
        """
        请求工具库信息
        
        Returns:
            工具列表
        """
        # 转发请求到工具库
        return await self.tool_registry.get_tool_info()
    
    async def request_search(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        请求搜索功能
        
        这个请求会被转发到工具库的AI agent，
        AI agent会调用所有search类工具，整理信息后返回。
        
        Args:
            query: 搜索查询
            context: 可选的上下文信息
        
        Returns:
            搜索结果
        """
        # 转发搜索请求到工具库的AI agent
        return await self.tool_registry.handle_search_request(query, context)
    
    async def request_tool_execution(
        self, 
        tool_id: str, 
        instruction: str, 
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        请求执行特定工具
        
        Args:
            tool_id: 工具ID
            instruction: 指令名称
            parameters: 参数
        
        Returns:
            执行结果
        """
        # 转发执行请求到工具库
        return self.tool_registry.execute_tool(tool_id, instruction, parameters)
