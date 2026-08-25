"""
插件系统
支持动态加载和管理插件
"""

import importlib.util
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional


class Plugin:
    """插件基类"""
    
    @property
    def name(self) -> str:
        """插件名称"""
        raise NotImplementedError
    
    @property
    def version(self) -> str:
        """插件版本"""
        raise NotImplementedError
    
    @property
    def description(self) -> str:
        """插件描述"""
        return ""
    
    def initialize(self) -> bool:
        """初始化插件"""
        return True
    
    def shutdown(self) -> bool:
        """关闭插件"""
        return True


class ToolPlugin(Plugin):
    """工具插件基类"""
    
    def get_metadata(self) -> Dict[str, Any]:
        """获取工具元数据"""
        raise NotImplementedError
    
    def execute(self, instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        raise NotImplementedError


class AIServicePlugin(Plugin):
    """AI服务插件基类"""
    
    async def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """处理请求"""
        raise NotImplementedError


class PluginManager:
    """插件管理器"""
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins: Dict[str, Plugin] = {}
        self.plugin_metadata: Dict[str, Dict[str, Any]] = {}
        
        # 确保插件目录存在
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
    
    def load_plugin(self, plugin_path: str) -> Optional[Plugin]:
        """加载单个插件"""
        try:
            path = Path(plugin_path)
            
            if not path.exists():
                print(f"插件文件不存在: {plugin_path}")
                return None
            
            # 动态导入模块
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                print(f"无法加载插件: {plugin_path}")
                return None
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 查找插件类（继承自Plugin的类）
            plugin_class = None
            for name, obj in inspect.getmembers(module):
                if inspect.isclass(obj) and issubclass(obj, Plugin) and obj != Plugin:
                    plugin_class = obj
                    break
            
            if plugin_class is None:
                print(f"插件中未找到Plugin子类: {plugin_path}")
                return None
            
            # 实例化插件
            plugin = plugin_class()
            
            # 初始化插件
            if not plugin.initialize():
                print(f"插件初始化失败: {plugin.name}")
                return None
            
            # 注册插件
            self.plugins[plugin.name] = plugin
            self.plugin_metadata[plugin.name] = {
                "name": plugin.name,
                "version": plugin.version,
                "description": plugin.description,
                "path": str(path),
                "type": self._get_plugin_type(plugin)
            }
            
            print(f"[OK] 插件加载成功: {plugin.name} v{plugin.version}")
            return plugin
            
        except Exception as e:
            print(f"加载插件失败 {plugin_path}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def load_plugins_from_directory(self, directory: str = None) -> int:
        """从目录加载所有插件"""
        if directory is None:
            directory = self.plugins_dir
        else:
            directory = Path(directory)
        
        if not directory.exists():
            print(f"插件目录不存在: {directory}")
            return 0
        
        count = 0
        for plugin_file in directory.glob("*.py"):
            if plugin_file.stem.startswith("_"):
                continue
            
            if self.load_plugin(str(plugin_file)):
                count += 1
        
        return count
    
    def unload_plugin(self, plugin_name: str) -> bool:
        """卸载插件"""
        if plugin_name not in self.plugins:
            return False
        
        plugin = self.plugins[plugin_name]
        
        # 关闭插件
        plugin.shutdown()
        
        # 移除插件
        del self.plugins[plugin_name]
        del self.plugin_metadata[plugin_name]
        
        print(f"插件已卸载: {plugin_name}")
        return True
    
    def get_plugin(self, plugin_name: str) -> Optional[Plugin]:
        """获取插件实例"""
        return self.plugins.get(plugin_name)
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有插件"""
        return list(self.plugin_metadata.values())
    
    def reload_plugin(self, plugin_name: str) -> bool:
        """重新加载插件"""
        if plugin_name not in self.plugin_metadata:
            return False
        
        plugin_path = self.plugin_metadata[plugin_name]["path"]
        
        # 卸载旧插件
        self.unload_plugin(plugin_name)
        
        # 重新加载
        return self.load_plugin(plugin_path) is not None
    
    def _get_plugin_type(self, plugin: Plugin) -> str:
        """获取插件类型"""
        if isinstance(plugin, ToolPlugin):
            return "tool"
        elif isinstance(plugin, AIServicePlugin):
            return "ai_service"
        else:
            return "unknown"
    
    def execute_tool_plugin(self, plugin_name: str, instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具插件"""
        plugin = self.get_plugin(plugin_name)
        
        if plugin is None:
            return {
                "success": False,
                "message": f"插件不存在: {plugin_name}",
                "error": "Plugin not found"
            }
        
        if not isinstance(plugin, ToolPlugin):
            return {
                "success": False,
                "message": f"插件不是工具插件: {plugin_name}",
                "error": "Not a tool plugin"
            }
        
        try:
            return plugin.execute(instruction, inputs)
        except Exception as e:
            return {
                "success": False,
                "message": f"插件执行失败: {str(e)}",
                "error": str(e)
            }


# 全局插件管理器实例
_plugin_manager = None


def get_plugin_manager(plugins_dir: str = "plugins") -> PluginManager:
    """获取全局插件管理器"""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager(plugins_dir)
    return _plugin_manager
