#!/usr/bin/env python3
"""
EAS 2.0 综合端到端测试
测试从前端输入到执行完成的完整流程
"""

import asyncio
import aiohttp
import json
import time
from datetime import datetime
from typing import Dict, Any, List
import sys


class TestRecorder:
    """测试结果记录器"""
    
    def __init__(self):
        self.results = []
        self.metrics = {
            "api_calls": 0,
            "api_successes": 0,
            "api_failures": 0,
            "total_time": 0,
            "steps_executed": 0,
            "steps_succeeded": 0,
            "steps_failed": 0,
            "tools_used": set(),
        }
        self.logs = []
    
    def log(self, message: str, level: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{level}] {message}"
        self.logs.append(log_entry)
        print(log_entry)
    
    def record_api_call(self, method: str, url: str, status: int, response_time: float, success: bool):
        """记录 API 调用"""
        self.metrics["api_calls"] += 1
        if success:
            self.metrics["api_successes"] += 1
        else:
            self.metrics["api_failures"] += 1
        
        self.results.append({
            "type": "api_call",
            "method": method,
            "url": url,
            "status": status,
            "response_time": response_time,
            "success": success,
            "timestamp": datetime.now().isoformat(),
        })
    
    def record_step_execution(self, step_id: str, description: str, tool: str, status: str, duration: float):
        """记录步骤执行"""
        self.metrics["steps_executed"] += 1
        if status == "COMPLETED":
            self.metrics["steps_succeeded"] += 1
        elif status == "FAILED":
            self.metrics["steps_failed"] += 1
        
        if tool:
            # Extract tool name without instruction
            tool_name = tool.split(":")[0] if ":" in tool else tool
            self.metrics["tools_used"].add(tool_name)
        
        self.results.append({
            "type": "step_execution",
            "step_id": step_id,
            "description": description,
            "tool": tool,
            "status": status,
            "duration": duration,
            "timestamp": datetime.now().isoformat(),
        })
    
    def generate_report(self) -> str:
        """生成测试报告"""
        report = []
        report.append("\n" + "="*70)
        report.append("EAS 2.0 综合测试报告")
        report.append("="*70)
        report.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # API 调用统计
        report.append("\n--- API 调用统计 ---")
        report.append(f"总调用次数: {self.metrics['api_calls']}")
        report.append(f"成功: {self.metrics['api_successes']}")
        report.append(f"失败: {self.metrics['api_failures']}")
        if self.metrics['api_calls'] > 0:
            success_rate = (self.metrics['api_successes'] / self.metrics['api_calls']) * 100
            report.append(f"成功率: {success_rate:.1f}%")
        
        # 步骤执行统计
        report.append("\n--- 步骤执行统计 ---")
        report.append(f"总步骤数: {self.metrics['steps_executed']}")
        report.append(f"成功: {self.metrics['steps_succeeded']}")
        report.append(f"失败: {self.metrics['steps_failed']}")
        if self.metrics['steps_executed'] > 0:
            success_rate = (self.metrics['steps_succeeded'] / self.metrics['steps_executed']) * 100
            report.append(f"成功率: {success_rate:.1f}%")
        
        # 工具使用统计
        report.append("\n--- 工具使用统计 ---")
        report.append(f"使用的工具数量: {len(self.metrics['tools_used'])}")
        report.append(f"工具列表: {', '.join(sorted(self.metrics['tools_used']))}")
        
        # 性能指标
        report.append("\n--- 性能指标 ---")
        report.append(f"总测试时间: {self.metrics['total_time']:.2f} 秒")
        
        # 详细结果
        report.append("\n--- 详细测试结果 ---")
        for i, result in enumerate(self.results, 1):
            if result["type"] == "api_call":
                status_icon = "[OK]" if result["success"] else "[FAIL]"
                report.append(f"{i}. {status_icon} API {result['method']} {result['url']}")
                report.append(f"   状态码: {result['status']}, 响应时间: {result['response_time']:.3f}s")
            elif result["type"] == "step_execution":
                status_icon = "[OK]" if result["status"] == "COMPLETED" else "[FAIL]"
                report.append(f"{i}. {status_icon} 步骤: {result['description']}")
                report.append(f"   工具: {result['tool']}, 状态: {result['status']}, 耗时: {result['duration']:.2f}s")
        
        report.append("\n" + "="*70)
        
        return "\n".join(report)


class E2ETestRunner:
    """端到端测试运行器"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.recorder = TestRecorder()
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def api_call(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """执行 API 调用并记录"""
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()
        
        try:
            async with self.session.request(method, url, **kwargs) as response:
                response_time = time.time() - start_time
                status = response.status
                
                try:
                    data = await response.json()
                except:
                    data = {"error": await response.text()}
                
                success = 200 <= status < 300
                self.recorder.record_api_call(method, endpoint, status, response_time, success)
                
                if not success:
                    self.recorder.log(f"API 调用失败: {method} {endpoint} - {status}", "ERROR")
                    self.recorder.log(f"响应: {data}", "ERROR")
                
                return {"status": status, "data": data, "success": success}
        
        except Exception as e:
            response_time = time.time() - start_time
            self.recorder.record_api_call(method, endpoint, 0, response_time, False)
            self.recorder.log(f"API 调用异常: {method} {endpoint} - {str(e)}", "ERROR")
            return {"status": 0, "data": {"error": str(e)}, "success": False}
    
    async def test_e2e_workflow(self) -> bool:
        """测试完整的端到端工作流"""
        self.recorder.log("开始端到端测试", "INFO")
        self.recorder.log("="*70, "INFO")
        
        # 测试任务描述 - 添加时间戳确保每次都是新的
        task_description = f"""创建一个简单的 Node.js Hello World 项目 (测试时间: {datetime.now().isoformat()})：
1. 初始化 Git 仓库
2. 创建项目目录 hello-world-test
3. 创建 package.json 文件，包含项目名称和版本
4. 创建 index.js 文件，输出 "Hello, World!"
5. 创建 README.md 文件，说明项目用途
"""
        
        # Phase 1: 提交设计任务
        self.recorder.log("\n[Phase 1] 提交设计任务", "INFO")
        design_response = await self.api_call(
            "POST",
            "/api/design",
            json={"user_request": task_description}
        )
        
        if not design_response["success"]:
            self.recorder.log("设计任务提交失败", "ERROR")
            return False
        
        design_id = design_response["data"].get("design", {}).get("design_id")
        if not design_id:
            self.recorder.log("未获取到 design_id", "ERROR")
            return False
        
        self.recorder.log(f"设计任务已提交: {design_id}", "SUCCESS")
        
        # 等待设计完成
        self.recorder.log("等待设计书生成...", "INFO")
        await asyncio.sleep(2)
        
        # 检查设计状态
        design_status_response = await self.api_call("GET", f"/api/design/{design_id}")
        if design_status_response["success"]:
            status = design_status_response["data"].get("status")
            self.recorder.log(f"设计书状态: {status}", "INFO")
        
        # Phase 2: 批准设计书
        self.recorder.log("\n[Phase 2] 批准设计书", "INFO")
        approve_response = await self.api_call(
            "POST",
            f"/api/design/{design_id}/approve",
            json={"design_id": design_id, "approved": True}
        )
        
        if not approve_response["success"]:
            self.recorder.log("设计书批准失败", "ERROR")
            return False
        
        self.recorder.log("设计书已批准", "SUCCESS")
        
        # Phase 3: 获取生成的计划 (approve 后自动生成)
        self.recorder.log("\n[Phase 3] 获取生成的执行计划", "INFO")
        
        plan_id = approve_response["data"].get("plan", {}).get("plan_id")
        if not plan_id:
            self.recorder.log("未获取到 plan_id", "ERROR")
            return False
        
        self.recorder.log(f"执行计划已生成: {plan_id}", "SUCCESS")
        
        # 等待计划生成完成
        self.recorder.log("等待执行计划生成...", "INFO")
        await asyncio.sleep(2)
        
        # 获取计划详情
        plan_detail_response = await self.api_call("GET", f"/api/plan/{plan_id}")
        if plan_detail_response["success"]:
            plan_data = plan_detail_response["data"]["plan"]
            steps = plan_data.get("steps", [])
            self.recorder.log(f"执行计划包含 {len(steps)} 个步骤", "INFO")
            
            # 记录步骤信息
            for step in steps[:5]:  # 只显示前5个
                self.recorder.log(f"  - 步骤 {step.get('order')}: {step.get('description')} (工具: {step.get('tool')})", "INFO")
        
        # Phase 4: 执行计划
        self.recorder.log("\n[Phase 4] 执行计划", "INFO")
        execute_response = await self.api_call(
            "POST",
            f"/api/plan/{plan_id}/execute"
        )
        
        if not execute_response["success"]:
            self.recorder.log("计划执行失败", "ERROR")
            return False
        
        self.recorder.log("计划执行已启动", "SUCCESS")
        
        # Phase 5: 监控执行进度
        self.recorder.log("\n[Phase 5] 监控执行进度", "INFO")
        max_wait = 120  # 最多等待2分钟
        wait_interval = 3
        elapsed = 0
        
        while elapsed < max_wait:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
            
            status_response = await self.api_call("GET", f"/api/plan/{plan_id}")
            if not status_response["success"]:
                self.recorder.log("无法获取计划状态", "ERROR")
                break

            plan_data = status_response["data"]["plan"]
            plan_status = plan_data.get("status")
            steps = plan_data.get("steps", [])
            
            # 统计步骤状态
            completed = sum(1 for s in steps if s.get("status") == "COMPLETED")
            failed = sum(1 for s in steps if s.get("status") == "FAILED")
            pending = sum(1 for s in steps if s.get("status") == "PENDING")
            running = sum(1 for s in steps if s.get("status") == "RUNNING")
            
            self.recorder.log(
                f"进度: 完成={completed}, 失败={failed}, 运行中={running}, 待处理={pending}",
                "INFO"
            )
            
            # 记录步骤执行
            for step in steps:
                if step.get("status") in ["COMPLETED", "FAILED"]:
                    # 检查是否已记录
                    step_id = step.get("id")
                    already_recorded = any(
                        r.get("step_id") == step_id 
                        for r in self.recorder.results 
                        if r.get("type") == "step_execution"
                    )
                    if not already_recorded:
                        self.recorder.record_step_execution(
                            step_id,
                            step.get("description", ""),
                            step.get("tool", ""),
                            step.get("status"),
                            0  # Duration not available
                        )
            
            # 检查是否完成
            if plan_status in ["COMPLETED", "FAILED", "CANCELLED"]:
                self.recorder.log(f"计划执行完成，状态: {plan_status}", "SUCCESS" if plan_status == "COMPLETED" else "ERROR")
                break
        else:
            self.recorder.log("等待超时", "ERROR")
            return False
        
        # Phase 6: 验证结果
        self.recorder.log("\n[Phase 6] 验证结果", "INFO")
        
        # 获取最终结果
        final_response = await self.api_call("GET", f"/api/plan/{plan_id}")
        if final_response["success"]:
            plan_data = final_response["data"]
            steps = plan_data.get("steps", [])
            
            success_count = sum(1 for s in steps if s.get("status") == "COMPLETED")
            fail_count = sum(1 for s in steps if s.get("status") == "FAILED")
            
            self.recorder.log(f"最终结果: {success_count} 成功, {fail_count} 失败", "INFO")
            
            # 检查失败的步骤
            if fail_count > 0:
                self.recorder.log("失败的步骤:", "WARN")
                for step in steps:
                    if step.get("status") == "FAILED":
                        self.recorder.log(f"  - {step.get('description')}: {step.get('error', 'Unknown error')}", "ERROR")
            
            # 判断测试是否通过
            test_passed = fail_count == 0 or (success_count / len(steps)) > 0.8
            
            if test_passed:
                self.recorder.log("\n端到端测试通过！", "SUCCESS")
            else:
                self.recorder.log(f"\n端到端测试失败，成功率: {(success_count/len(steps))*100:.1f}%", "ERROR")
            
            return test_passed
        
        return False
    
    async def run_all_tests(self):
        """运行所有测试"""
        start_time = time.time()
        
        self.recorder.log("EAS 2.0 综合测试开始", "INFO")
        self.recorder.log("="*70, "INFO")
        
        # 运行端到端测试
        e2e_passed = await self.test_e2e_workflow()
        
        # 记录总时间
        self.recorder.metrics["total_time"] = time.time() - start_time
        
        # 生成报告
        report = self.recorder.generate_report()
        print(report)
        
        # 保存报告到文件
        report_file = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
            f.write("\n\n--- 详细日志 ---\n")
            f.write("\n".join(self.recorder.logs))
        
        self.recorder.log(f"\n报告已保存到: {report_file}", "INFO")
        
        return e2e_passed


async def main():
    """主函数"""
    print("\n" + "="*70)
    print("EAS 2.0 综合端到端测试")
    print("="*70)
    
    # 检查服务是否运行
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8000/api/status") as response:
                if response.status == 200:
                    print("[OK] Backend 服务正在运行")
                else:
                    print("[FAIL] Backend 服务未正常响应")
                    return 1
    except Exception as e:
        print(f"[FAIL] 无法连接到 Backend 服务: {e}")
        return 1
    
    # 运行测试
    async with E2ETestRunner() as runner:
        success = await runner.run_all_tests()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
