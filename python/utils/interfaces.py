"""
插件化接口定义（Python层）

所有AI服务都实现这些抽象类，支持插件扩展
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from models import DesignDocument, ExecutionPlan, Step, ToolSchema


class AIService(ABC):
    """AI服务基类 - Designer, ToolManager, Knowledge AI都继承此类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """服务名称"""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """服务版本"""
        pass
    
    @abstractmethod
    async def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """处理请求"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass


class Designer(AIService):
    """设计师AI接口"""
    
    @abstractmethod
    async def generate_design(self, user_request: str) -> DesignDocument:
        """生成设计书（Phase 1）"""
        pass
    
    @abstractmethod
    async def generate_plan(self, design: DesignDocument) -> ExecutionPlan:
        """生成执行计划（Phase 2）"""
        pass
    
    @abstractmethod
    async def assess_severity(self, issue: str, context: Dict[str, Any]) -> int:
        """评估问题严重程度（Level 1-4）"""
        pass
    
    @abstractmethod
    async def generate_summary(self, plan: ExecutionPlan, results: List[Step]) -> str:
        """生成总结报告"""
        pass


class ToolManagerAI(AIService):
    """工具管理AI接口"""
    
    @abstractmethod
    async def assess_tool(self, tool_id: str, tool_schema: ToolSchema) -> Dict[str, Any]:
        """评估工具可用性和质量"""
        pass
    
    @abstractmethod
    async def confirm_tool_issue(self, tool_id: str, error: str) -> Dict[str, Any]:
        """确认工具问题"""
        pass
    
    @abstractmethod
    async def suggest_alternatives(self, tool_id: str) -> List[str]:
        """建议替代工具"""
        pass


class KnowledgeAI(AIService):
    """知识助手AI接口"""
    
    @abstractmethod
    async def query(self, question: str, context: Optional[Dict[str, Any]] = None) -> str:
        """回答问题"""
        pass
    
    @abstractmethod
    async def analyze_error(self, error: str, step: Step) -> Dict[str, Any]:
        """分析错误"""
        pass
    
    @abstractmethod
    async def suggest_fix(self, issue: str, context: Dict[str, Any]) -> str:
        """建议修复方案"""
        pass


class ExecutorAI(AIService):
    """执行器AI接口（可选，也可以用纯逻辑实现）"""
    
    @abstractmethod
    async def execute_step(self, step: Step) -> Dict[str, Any]:
        """执行单个步骤"""
        pass
    
    @abstractmethod
    async def check_result(self, step: Step, output: Dict[str, Any]) -> bool:
        """检查执行结果是否符合预期"""
        pass


class Plugin(ABC):
    """插件接口 - 第三方扩展实现此接口"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """插件版本"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """插件描述"""
        pass
    
    @abstractmethod
    async def initialize(self) -> None:
        """初始化插件"""
        pass
    
    @abstractmethod
    async def before_design(self, request: str) -> Optional[Dict[str, Any]]:
        """钩子：设计书生成前"""
        pass
    
    @abstractmethod
    async def after_design(self, design: DesignDocument) -> Optional[Dict[str, Any]]:
        """钩子：设计书生成后"""
        pass
    
    @abstractmethod
    async def before_step(self, step: Step) -> Optional[Dict[str, Any]]:
        """钩子：步骤执行前"""
        pass
    
    @abstractmethod
    async def after_step(self, step: Step, output: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """钩子：步骤执行后"""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """关闭插件"""
        pass


class PluginManager(ABC):
    """插件管理器接口"""
    
    @abstractmethod
    async def load_plugin(self, plugin: Plugin) -> None:
        """加载插件"""
        pass
    
    @abstractmethod
    async def unload_plugin(self, name: str) -> None:
        """卸载插件"""
        pass
    
    @abstractmethod
    def list_plugins(self) -> List[str]:
        """列出所有插件"""
        pass
    
    @abstractmethod
    async def trigger_before_design(self, request: str) -> List[Dict[str, Any]]:
        """触发设计前钩子"""
        pass
    
    @abstractmethod
    async def trigger_after_design(self, design: DesignDocument) -> List[Dict[str, Any]]:
        """触发设计后钩子"""
        pass
    
    @abstractmethod
    async def trigger_before_step(self, step: Step) -> List[Dict[str, Any]]:
        """触发步骤执行前钩子"""
        pass
    
    @abstractmethod
    async def trigger_after_step(self, step: Step, output: Dict[str, Any]) -> List[Dict[str, Any]]:
        """触发步骤执行后钩子"""
        pass
