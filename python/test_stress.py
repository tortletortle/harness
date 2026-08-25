#!/usr/bin/env python3
"""
EAS 2.0 压力测试 (Stress Testing)
测试系统在高负载下的表现
"""

import asyncio
import aiohttp
import time
import sys
from datetime import datetime
from typing import List, Dict, Any
import statistics


class StressTest:
    """压力测试"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.results = []
    
    def log(self, message: str, status: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "INFO": "[INFO]", "WARN": "[WARN]"}.get(status, "[*]")
        print(f"[{timestamp}] {icon} {message}")
    
    async def concurrent_design_submissions(self, count: int = 10) -> Dict[str, Any]:
        """测试 1: 并发提交设计任务"""
        self.log(f"测试并发提交 {count} 个设计任务...", "INFO")
        
        start_time = time.time()
        successes = 0
        failures = 0
        response_times = []
        
        async def submit_design(session, task_id):
            """提交单个设计任务"""
            task_start = time.time()
            try:
                payload = {
                    "user_request": f"测试任务 {task_id}: 创建一个简单的文件 test_{task_id}.txt"
                }
                
                async with session.post(f"{self.base_url}/api/design", json=payload) as response:
                    response_time = time.time() - task_start
                    response_times.append(response_time)
                    
                    if response.status == 200:
                        data = await response.json()
                        if data.get("success") and data.get("design", {}).get("design_id"):
                            return {"success": True, "response_time": response_time, "task_id": task_id}
                    
                    return {"success": False, "response_time": response_time, "task_id": task_id, "status": response.status}
            
            except Exception as e:
                response_time = time.time() - task_start
                response_times.append(response_time)
                return {"success": False, "response_time": response_time, "task_id": task_id, "error": str(e)}
        
        # 并发提交
        async with aiohttp.ClientSession() as session:
            tasks = [submit_design(session, i) for i in range(count)]
            results = await asyncio.gather(*tasks)
        
        # 统计结果
        successes = sum(1 for r in results if r.get("success"))
        failures = count - successes
        total_time = time.time() - start_time
        
        # 计算性能指标
        avg_response_time = statistics.mean(response_times) if response_times else 0
        min_response_time = min(response_times) if response_times else 0
        max_response_time = max(response_times) if response_times else 0
        
        # 输出结果
        self.log(f"并发提交完成: {successes} 成功, {failures} 失败", "INFO")
        self.log(f"总耗时: {total_time:.2f}s", "INFO")
        self.log(f"平均响应时间: {avg_response_time:.3f}s", "INFO")
        self.log(f"响应时间范围: {min_response_time:.3f}s - {max_response_time:.3f}s", "INFO")
        
        success_rate = (successes / count) * 100
        
        if success_rate >= 80:
            self.log(f"并发测试通过: 成功率 {success_rate:.1f}%", "PASS")
            passed = True
        else:
            self.log(f"并发测试失败: 成功率 {success_rate:.1f}%", "FAIL")
            passed = False
        
        return {
            "test": "concurrent_submissions",
            "passed": passed,
            "successes": successes,
            "failures": failures,
            "total_time": total_time,
            "avg_response_time": avg_response_time,
            "min_response_time": min_response_time,
            "max_response_time": max_response_time,
            "success_rate": success_rate,
        }
    
    async def rapid_sequential_requests(self, count: int = 20) -> Dict[str, Any]:
        """测试 2: 快速连续请求"""
        self.log(f"测试快速连续提交 {count} 个请求...", "INFO")
        
        start_time = time.time()
        successes = 0
        failures = 0
        response_times = []
        
        async with aiohttp.ClientSession() as session:
            for i in range(count):
                task_start = time.time()
                try:
                    payload = {
                        "user_request": f"快速测试任务 {i}: 创建文件 rapid_{i}.txt"
                    }
                    
                    async with session.post(f"{self.base_url}/api/design", json=payload) as response:
                        response_time = time.time() - task_start
                        response_times.append(response_time)
                        
                        if response.status == 200:
                            data = await response.json()
                            if data.get("success") and data.get("design", {}).get("design_id"):
                                successes += 1
                            else:
                                failures += 1
                        else:
                            failures += 1
                
                except Exception as e:
                    response_time = time.time() - task_start
                    response_times.append(response_time)
                    failures += 1
                
                # 非常短的间隔
                await asyncio.sleep(0.1)
        
        total_time = time.time() - start_time
        avg_response_time = statistics.mean(response_times) if response_times else 0
        
        self.log(f"快速连续请求完成: {successes} 成功, {failures} 失败", "INFO")
        self.log(f"总耗时: {total_time:.2f}s", "INFO")
        self.log(f"平均响应时间: {avg_response_time:.3f}s", "INFO")
        
        success_rate = (successes / count) * 100
        
        if success_rate >= 80:
            self.log(f"快速连续请求测试通过: 成功率 {success_rate:.1f}%", "PASS")
            passed = True
        else:
            self.log(f"快速连续请求测试失败: 成功率 {success_rate:.1f}%", "FAIL")
            passed = False
        
        return {
            "test": "rapid_sequential",
            "passed": passed,
            "successes": successes,
            "failures": failures,
            "total_time": total_time,
            "avg_response_time": avg_response_time,
            "success_rate": success_rate,
        }
    
    async def large_input_test(self) -> Dict[str, Any]:
        """测试 3: 大型输入处理"""
        self.log("测试大型输入处理...", "INFO")
        
        # 生成一个很长的任务描述
        large_description = """创建一个完整的 Web 应用程序项目：

项目名称: ComplexWebApp

技术栈:
- 前端: React 18 + TypeScript + Vite
- 后端: Node.js + Express + TypeScript
- 数据库: PostgreSQL
- ORM: Prisma
- 认证: JWT
- API 文档: Swagger/OpenAPI

项目结构:
1. 初始化 Git 仓库
2. 创建前端项目目录 frontend/
3. 创建后端项目目录 backend/
4. 创建数据库配置目录 database/

前端要求:
- 设置 React + TypeScript + Vite 环境
- 安装依赖: react, react-dom, react-router-dom, axios, @tanstack/react-query
- 创建基础目录结构: src/components, src/pages, src/hooks, src/utils, src/api
- 创建 App.tsx 主应用组件
- 创建 Router 配置
- 设置 Tailwind CSS
- 创建基础布局组件: Header, Footer, Sidebar
- 创建页面: Home, Login, Dashboard, Profile, Settings
- 实现 API 客户端封装
- 实现认证状态管理
- 配置环境变量

后端要求:
- 设置 Node.js + Express + TypeScript 环境
- 安装依赖: express, prisma, jsonwebtoken, bcrypt, cors, dotenv, helmet
- 创建目录结构: src/routes, src/controllers, src/services, src/middleware, src/utils
- 实现用户认证 API (注册、登录、登出)
- 实现用户管理 API (CRUD)
- 实现 JWT 中间件
- 实现错误处理中间件
- 配置 Prisma ORM
- 创建数据库 schema (User, Post, Comment 模型)
- 实现数据验证
- 配置 CORS
- 配置安全头部 (helmet)
- 创建 Swagger API 文档

数据库:
- 创建 Prisma schema
- 定义 User 模型 (id, email, password, name, createdAt, updatedAt)
- 定义 Post 模型 (id, title, content, authorId, published, createdAt, updatedAt)
- 定义 Comment 模型 (id, content, postId, authorId, createdAt, updatedAt)
- 设置关系: User -> Posts (一对多), User -> Comments (一对多), Post -> Comments (一对多)

配置文件:
- 创建 .env.example 文件
- 创建 .gitignore 文件
- 创建 docker-compose.yml (PostgreSQL 服务)
- 创建 README.md 文档
- 创建 package.json 工作区配置

测试:
- 前端: 安装 Vitest + React Testing Library
- 后端: 安装 Jest + Supertest
- 创建示例测试文件

部署:
- 创建 Dockerfile (前端和后端)
- 创建 .dockerignore
- 创建部署脚本

请为这个项目生成完整的代码和配置。
""" * 3  # 重复3次使其更大
        
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"user_request": large_description}
                
                async with session.post(f"{self.base_url}/api/design", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as response:
                    response_time = time.time() - start_time
                    
                    if response.status == 200:
                        data = await response.json()
                        if data.get("success") and data.get("design", {}).get("design_id"):
                            self.log(f"大型输入处理成功: {len(large_description)} 字符, 耗时 {response_time:.2f}s", "PASS")
                            return {
                                "test": "large_input",
                                "passed": True,
                                "input_size": len(large_description),
                                "response_time": response_time,
                            }
                    
                    self.log(f"大型输入处理失败: 状态码 {response.status}", "FAIL")
                    return {
                        "test": "large_input",
                        "passed": False,
                        "input_size": len(large_description),
                        "response_time": response_time,
                        "status": response.status,
                    }
        
        except Exception as e:
            response_time = time.time() - start_time
            self.log(f"大型输入处理失败: {str(e)}", "FAIL")
            return {
                "test": "large_input",
                "passed": False,
                "input_size": len(large_description),
                "response_time": response_time,
                "error": str(e),
            }
    
    async def edge_case_test(self) -> Dict[str, Any]:
        """测试 4: 边界情况"""
        self.log("测试边界情况...", "INFO")
        
        test_cases = [
            {"name": "空描述", "description": ""},
            {"name": "特殊字符", "description": "创建文件 @#$%^&*()_+{}[]|\\:\";<>?,./"},
            {"name": "中文描述", "description": "创建一个包含中文名称的文件：测试文件.txt"},
            {"name": "超长单词", "description": "create file " + "a" * 1000 + ".txt"},
            {"name": "换行符", "description": "创建文件\n包含\n多个\n换行符"},
        ]
        
        results = []
        
        async with aiohttp.ClientSession() as session:
            for test_case in test_cases:
                try:
                    payload = {"user_request": test_case["description"]}
                    
                    async with session.post(f"{self.base_url}/api/design", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        success = response.status == 200
                        
                        if success:
                            data = await response.json()
                            success = data.get("success") and bool(data.get("design", {}).get("design_id"))
                        
                        results.append({
                            "name": test_case["name"],
                            "success": success,
                            "status": response.status,
                        })
                        
                        status_icon = "[OK]" if success else "[FAIL]"
                        self.log(f"  {status_icon} {test_case['name']}: {'通过' if success else '失败'}", "INFO")
                
                except Exception as e:
                    results.append({
                        "name": test_case["name"],
                        "success": False,
                        "error": str(e),
                    })
                    self.log(f"  [FAIL] {test_case['name']}: 异常 {str(e)}", "INFO")
                
                await asyncio.sleep(0.5)
        
        successes = sum(1 for r in results if r.get("success"))
        total = len(results)
        success_rate = (successes / total) * 100
        
        # 边界情况测试允许部分失败
        passed = success_rate >= 60
        
        if passed:
            self.log(f"边界情况测试通过: {successes}/{total} 成功", "PASS")
        else:
            self.log(f"边界情况测试失败: {successes}/{total} 成功", "FAIL")
        
        return {
            "test": "edge_cases",
            "passed": passed,
            "successes": successes,
            "total": total,
            "success_rate": success_rate,
            "details": results,
        }
    
    async def run_all(self):
        """运行所有压力测试"""
        print("\n" + "="*70)
        print("EAS 2.0 压力测试")
        print("="*70 + "\n")
        
        all_results = []
        
        # 测试 1: 并发提交
        self.log("\n=== 测试 1: 并发任务提交 ===", "INFO")
        result1 = await self.concurrent_design_submissions(count=10)
        all_results.append(result1)
        
        await asyncio.sleep(2)
        
        # 测试 2: 快速连续请求
        self.log("\n=== 测试 2: 快速连续请求 ===", "INFO")
        result2 = await self.rapid_sequential_requests(count=20)
        all_results.append(result2)
        
        await asyncio.sleep(2)
        
        # 测试 3: 大型输入
        self.log("\n=== 测试 3: 大型输入处理 ===", "INFO")
        result3 = await self.large_input_test()
        all_results.append(result3)
        
        await asyncio.sleep(2)
        
        # 测试 4: 边界情况
        self.log("\n=== 测试 4: 边界情况测试 ===", "INFO")
        result4 = await self.edge_case_test()
        all_results.append(result4)
        
        # 输出总结
        print("\n" + "="*70)
        print("压力测试总结")
        print("="*70)
        
        passed = sum(1 for r in all_results if r.get("passed"))
        total = len(all_results)
        
        print(f"\n通过: {passed}/{total}")
        print(f"失败: {total - passed}/{total}")
        
        print("\n详细结果:")
        for result in all_results:
            test_name = result.get("test", "unknown")
            passed_flag = "[OK]" if result.get("passed") else "[FAIL]"
            print(f"  {passed_flag} {test_name}")
            
            if "success_rate" in result:
                print(f"     成功率: {result['success_rate']:.1f}%")
            if "avg_response_time" in result:
                print(f"     平均响应时间: {result['avg_response_time']:.3f}s")
        
        print("="*70)
        
        if passed == total:
            print("[OK] 所有压力测试通过！系统在高负载下表现良好。")
            return True
        elif passed >= total * 0.75:
            print("[WARN] 大部分压力测试通过，但仍有改进空间。")
            return True
        else:
            print("[FAIL] 压力测试失败较多，系统可能无法处理高负载。")
            return False


async def main():
    """主函数"""
    stress_test = StressTest()
    success = await stress_test.run_all()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
