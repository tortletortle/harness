"""
Web资源获取工具
用于下载文件和获取网页内容
"""

import urllib.request
from pathlib import Path
from typing import Any, Dict


class WebFetcher:
    """Web资源获取工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "web_fetcher",
            "name": "Web资源获取工具",
            "description": "下载文件和获取网页内容",
            "category": "search",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "download_file",
                        "description": "下载文件",
                        "parameters": {
                            "url": "文件URL",
                            "path": "保存路径"
                        }
                    },
                    {
                        "name": "fetch_content",
                        "description": "获取网页内容",
                        "parameters": {
                            "url": "网页URL"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def download_file(url: str, path: str) -> Dict[str, Any]:
        """下载文件"""
        try:
            file_path = Path(path)
            
            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 下载文件
            urllib.request.urlretrieve(url, str(file_path))
            
            # 获取文件大小
            size = file_path.stat().st_size
            
            return {
                "success": True,
                "message": f"文件下载成功: {path}",
                "path": str(file_path.absolute()),
                "size": size
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"下载失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def fetch_content(url: str) -> Dict[str, Any]:
        """获取网页内容"""
        try:
            with urllib.request.urlopen(url) as response:
                content = response.read().decode('utf-8')
                
            return {
                "success": True,
                "message": "内容获取成功",
                "content": content,
                "size": len(content)
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"获取内容失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        if instruction == "download_file":
            return WebFetcher.download_file(
                url=inputs.get("url", ""),
                path=inputs.get("path", "")
            )
        elif instruction == "fetch_content":
            return WebFetcher.fetch_content(url=inputs.get("url", ""))
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
