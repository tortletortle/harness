#!/usr/bin/env python3
"""
EAS 2.0 烟雾测试 (Smoke Testing)
快速验证核心功能是否正常工作
"""

import asyncio
import aiohttp
import sys
import os
from datetime import datetime


class SmokeTest:
    """烟雾测试"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.passed = 0
        self.failed = 0
        self.tests = []
    
    def log(self, message: str, status: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "INFO": "[INFO]"}.get(status, "[*]")
        print(f"[{timestamp}] {icon} {message}")
    
    async def test_api_health(self, session) -> bool:
        """测试 1: API 健康检查"""
        self.log("测试 API 健康检查...", "INFO")
        
        try:
            async with session.get(f"{self.base_url}/api/status") as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("status") == "running":
                        self.log("API 健康检查通过", "PASS")
                        return True
                
                self.log(f"API 健康检查失败: 状态码 {response.status}", "FAIL")
                return False
        except Exception as e:
            self.log(f"API 健康检查失败: {str(e)}", "FAIL")
            return False
    
    async def test_database_connection(self, session) -> bool:
        """测试 2: 数据库连接"""
        self.log("测试数据库连接...", "INFO")
        
        try:
            async with session.get(f"{self.base_url}/api/designs") as response:
                if response.status in [200, 404]:  # 200 或 404 都说明能连接数据库
                    self.log("数据库连接正常", "PASS")
                    return True
                
                self.log(f"数据库连接失败: 状态码 {response.status}", "FAIL")
                return False
        except Exception as e:
            self.log(f"数据库连接失败: {str(e)}", "FAIL")
            return False
    
    async def test_tool_registry(self, session) -> bool:
        """测试 3: 工具注册表"""
        self.log("测试工具注册表...", "INFO")
        
        try:
            # 通过状态接口检查工具数量
            async with session.get(f"{self.base_url}/api/status") as response:
                if response.status == 200:
                    data = await response.json()
                    tools_count = data.get("tools_count", 0)
                    
                    if tools_count >= 5:
                        self.log(f"工具注册表正常: {tools_count} 个工具已注册", "PASS")
                        return True
                    else:
                        self.log(f"工具注册表异常: 只有 {tools_count} 个工具", "FAIL")
                        return False
                
                self.log(f"工具注册表检查失败: 状态码 {response.status}", "FAIL")
                return False
        except Exception as e:
            self.log(f"工具注册表检查失败: {str(e)}", "FAIL")
            return False
    
    async def test_ai_services(self, session) -> bool:
        """测试 4: AI 服务连接"""
        self.log("测试 AI 服务连接...", "INFO")
        
        try:
            async with session.get(f"{self.base_url}/api/status") as response:
                if response.status == 200:
                    data = await response.json()
                    providers = data.get("llm_providers", [])
                    
                    if len(providers) > 0:
                        self.log(f"AI 服务连接正常: {len(providers)} 个提供商可用 ({', '.join(providers)})", "PASS")
                        return True
                    else:
                        self.log("AI 服务连接异常: 没有可用的提供商", "FAIL")
                        return False
                
                self.log(f"AI 服务检查失败: 状态码 {response.status}", "FAIL")
                return False
        except Exception as e:
            self.log(f"AI 服务检查失败: {str(e)}", "FAIL")
            return False
    
    async def test_simple_design_creation(self, session) -> bool:
        """测试 5: 简单设计创建"""
        self.log("测试简单设计创建...", "INFO")
        
        try:
            # 创建一个简单的设计任务
            payload = {
                "user_request": "创建一个测试文件 test.txt，内容为 'Hello, Smoke Test!'"
            }
            
            async with session.post(f"{self.base_url}/api/design", json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    design_id = data.get("design", {}).get("design_id")
                    
                    if design_id:
                        self.log(f"简单设计创建成功: {design_id}", "PASS")
                        return True
                    else:
                        self.log("简单设计创建失败: 未返回 design_id", "FAIL")
                        return False
                
                self.log(f"简单设计创建失败: 状态码 {response.status}", "FAIL")
                return False
        except Exception as e:
            self.log(f"简单设计创建失败: {str(e)}", "FAIL")
            return False
    
    async def test_executor_import(self) -> bool:
        """测试 6: Executor 模块导入"""
        self.log("测试 Executor 模块导入...", "INFO")
        
        try:
            # 添加路径
            sys.path.insert(0, os.path.dirname(__file__))
            
            from executor import DefaultExecutor
            from tools import get_registry
            
            # 尝试创建实例
            registry = get_registry()
            executor = DefaultExecutor(None, {}, registry)
            
            # 检查 _infer_instruction 方法是否存在
            if hasattr(executor, '_infer_instruction'):
                self.log("Executor 模块导入成功，修复已应用", "PASS")
                return True
            else:
                self.log("Executor 模块导入成功，但缺少 _infer_instruction 方法", "FAIL")
                return False
        
        except Exception as e:
            self.log(f"Executor 模块导入失败: {str(e)}", "FAIL")
            return False
    
    async def run_all(self):
        """运行所有烟雾测试"""
        print("\n" + "="*70)
        print("EAS 2.0 烟雾测试")
        print("="*70 + "\n")
        
        results = []
        
        # 运行非 API 测试
        results.append(("Executor 模块导入", await self.test_executor_import()))
        
        # 运行 API 测试
        async with aiohttp.ClientSession() as session:
            results.append(("API 健康检查", await self.test_api_health(session)))
            results.append(("数据库连接", await self.test_database_connection(session)))
            results.append(("工具注册表", await self.test_tool_registry(session)))
            results.append(("AI 服务连接", await self.test_ai_services(session)))
            results.append(("简单设计创建", await self.test_simple_design_creation(session)))
        
        # 统计结果
        passed = sum(1 for _, result in results if result)
        failed = sum(1 for _, result in results if not result)
        
        # 输出总结
        print("\n" + "="*70)
        print("烟雾测试总结")
        print("="*70)
        print(f"通过: {passed}/{len(results)}")
        print(f"失败: {failed}/{len(results)}")
        
        if failed > 0:
            print("\n失败的测试:")
            for name, result in results:
                if not result:
                    print(f"  [FAIL] {name}")
        
        print("="*70)
        
        if failed == 0:
            print("[OK] 所有烟雾测试通过！系统基本功能正常。")
            return True
        else:
            print("[FAIL] 部分烟雾测试失败，系统可能存在问题。")
            return False


async def main():
    """主函数"""
    smoke_test = SmokeTest()
    success = await smoke_test.run_all()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
