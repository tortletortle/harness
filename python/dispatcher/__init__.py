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
    
    def __init__(self, tool_registry: ToolRegistry, capability_registry=None):
        """
        初始化调度器
        
        Args:
            tool_registry: 工具注册表实例
            capability_registry: 可选，统一 Capability 注册表。
                kind=agent_tool 的能力会合并进工具目录（request_tool_info），
                让 Designer/前端能发现 Agent 工具；不传时仅暴露普通工具。
        """
        self.tool_registry = tool_registry
        self.capability_registry = capability_registry
    
    async def request_tool_info(self) -> List[Dict[str, Any]]:
        """
        请求工具库信息（普通工具 + agent_tool 能力合并目录）
        
        Returns:
            工具列表
        """
        # 转发请求到工具库
        items: List[Dict[str, Any]] = list(await self.tool_registry.get_tool_info())
        # Agent 即工具：把 kind=agent_tool 的 Capability 拼进目录，
        # 让 Designer 的工具评估与计划生成能看到 Agent 工具。
        if self.capability_registry is not None:
            try:
                known = {item.get("tool_id") for item in items}
                for spec in self.capability_registry.list_specs():
                    if spec.kind != "agent_tool" or spec.id in known:
                        continue
                    description = spec.description or "Agent 工具（单轮、Grant 受限）"
                    items.append({
                        "tool_id": spec.id,
                        "name": spec.id,
                        "description": f"[agent_tool] {description}",
                        "category": "agent",
                        "kind": "agent_tool",
                        "capabilities": {"instructions": []},
                    })
            except Exception:
                # 目录聚合失败不阻断普通工具信息。
                pass
        return items
    
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
