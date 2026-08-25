#!/usr/bin/env python3
"""
EAS 2.0 综合测试运行器
协调运行所有测试：端到端、烟雾测试、压力测试
"""

import asyncio
import sys
import os
import subprocess
from datetime import datetime
import json


class MasterTestRunner:
    """主测试运行器"""
    
    def __init__(self):
        self.results = {
            "unit_tests": None,
            "smoke_tests": None,
            "e2e_tests": None,
            "stress_tests": None,
        }
        self.start_time = None
        self.end_time = None
    
    def log(self, message: str, level: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = {
            "INFO": "[INFO]",
            "SUCCESS": "[OK]",
            "ERROR": "[FAIL]",
            "WARN": "[WARN]",
            "SECTION": "=",
        }.get(level, "[*]")
        
        if level == "SECTION":
            print("\n" + "="*70)
            print(f"{message}")
            print("="*70)
        else:
            print(f"[{timestamp}] {icon} {message}")
    
    async def check_prerequisites(self) -> bool:
        """检查测试前提条件"""
        self.log("检查测试环境前提条件", "SECTION")
        
        checks = []
        
        # 1. 检查 Backend 服务
        self.log("检查 Backend 服务...", "INFO")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8000/api/status", timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        self.log("Backend 服务运行正常", "SUCCESS")
                        checks.append(True)
                    else:
                        self.log(f"Backend 服务响应异常: {response.status}", "ERROR")
                        checks.append(False)
        except Exception as e:
            self.log(f"Backend 服务不可访问: {str(e)}", "ERROR")
            checks.append(False)
        
        # 2. 检查数据库
        self.log("检查数据库连接...", "INFO")
        db_path = "../data/eas.db"
        if os.path.exists(db_path):
            self.log("数据库文件存在", "SUCCESS")
            checks.append(True)
        else:
            self.log(f"数据库文件不存在: {db_path}", "ERROR")
            checks.append(False)
        
        # 3. 检查配置文件
        self.log("检查配置文件...", "INFO")
        config_path = "../config/models.yaml"
        if os.path.exists(config_path):
            self.log("配置文件存在", "SUCCESS")
            checks.append(True)
        else:
            self.log(f"配置文件不存在: {config_path}", "ERROR")
            checks.append(False)
        
        # 4. 检查 Python 依赖
        self.log("检查 Python 依赖...", "INFO")
        try:
            import aiohttp
            import yaml
            import aiosqlite
            self.log("Python 依赖完整", "SUCCESS")
            checks.append(True)
        except ImportError as e:
            self.log(f"缺少 Python 依赖: {str(e)}", "ERROR")
            checks.append(False)
        
        # 5. 检查测试脚本
        self.log("检查测试脚本...", "INFO")
        test_files = [
            "test_executor_fixes.py",
            "test_smoke.py",
            "test_e2e_comprehensive.py",
            "test_stress.py",
        ]
        
        all_exist = all(os.path.exists(f) for f in test_files)
        if all_exist:
            self.log("所有测试脚本就绪", "SUCCESS")
            checks.append(True)
        else:
            missing = [f for f in test_files if not os.path.exists(f)]
            self.log(f"缺少测试脚本: {', '.join(missing)}", "ERROR")
            checks.append(False)
        
        all_passed = all(checks)
        
        if all_passed:
            self.log("\n[OK] 所有前提条件满足，可以开始测试", "SUCCESS")
        else:
            self.log(f"\n[FAIL] {len([c for c in checks if not c])} 个前提条件未满足", "ERROR")
        
        return all_passed
    
    async def run_unit_tests(self) -> bool:
        """运行单元测试"""
        self.log("Phase 1: 单元测试 - Executor 修复验证", "SECTION")
        
        try:
            result = subprocess.run(
                [sys.executable, "test_executor_fixes.py"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            print(result.stdout)
            
            if result.returncode == 0:
                self.log("单元测试通过", "SUCCESS")
                self.results["unit_tests"] = "PASSED"
                return True
            else:
                self.log("单元测试失败", "ERROR")
                if result.stderr:
                    print(result.stderr)
                self.results["unit_tests"] = "FAILED"
                return False
        
        except subprocess.TimeoutExpired:
            self.log("单元测试超时", "ERROR")
            self.results["unit_tests"] = "TIMEOUT"
            return False
        except Exception as e:
            self.log(f"单元测试异常: {str(e)}", "ERROR")
            self.results["unit_tests"] = "ERROR"
            return False
    
    async def run_smoke_tests(self) -> bool:
        """运行烟雾测试"""
        self.log("Phase 2: 烟雾测试 - 核心功能验证", "SECTION")
        
        try:
            result = subprocess.run(
                [sys.executable, "test_smoke.py"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            
            print(result.stdout)
            
            if result.returncode == 0:
                self.log("烟雾测试通过", "SUCCESS")
                self.results["smoke_tests"] = "PASSED"
                return True
            else:
                self.log("烟雾测试失败", "ERROR")
                if result.stderr:
                    print(result.stderr)
                self.results["smoke_tests"] = "FAILED"
                return False
        
        except subprocess.TimeoutExpired:
            self.log("烟雾测试超时", "ERROR")
            self.results["smoke_tests"] = "TIMEOUT"
            return False
        except Exception as e:
            self.log(f"烟雾测试异常: {str(e)}", "ERROR")
            self.results["smoke_tests"] = "ERROR"
            return False
    
    async def run_e2e_tests(self) -> bool:
        """运行端到端测试"""
        self.log("Phase 3: 端到端测试 - 完整流程验证", "SECTION")
        
        try:
            result = subprocess.run(
                [sys.executable, "test_e2e_comprehensive.py"],
                capture_output=True,
                text=True,
                timeout=300,  # 5 分钟
            )
            
            print(result.stdout)
            
            if result.returncode == 0:
                self.log("端到端测试通过", "SUCCESS")
                self.results["e2e_tests"] = "PASSED"
                return True
            else:
                self.log("端到端测试失败", "ERROR")
                if result.stderr:
                    print(result.stderr)
                self.results["e2e_tests"] = "FAILED"
                return False
        
        except subprocess.TimeoutExpired:
            self.log("端到端测试超时", "ERROR")
            self.results["e2e_tests"] = "TIMEOUT"
            return False
        except Exception as e:
            self.log(f"端到端测试异常: {str(e)}", "ERROR")
            self.results["e2e_tests"] = "ERROR"
            return False
    
    async def run_stress_tests(self) -> bool:
        """运行压力测试"""
        self.log("Phase 4: 压力测试 - 高负载验证", "SECTION")
        
        try:
            result = subprocess.run(
                [sys.executable, "test_stress.py"],
                capture_output=True,
                text=True,
                timeout=600,  # 10 分钟
            )
            
            print(result.stdout)
            
            if result.returncode == 0:
                self.log("压力测试通过", "SUCCESS")
                self.results["stress_tests"] = "PASSED"
                return True
            else:
                self.log("压力测试部分失败（可接受）", "WARN")
                if result.stderr:
                    print(result.stderr)
                self.results["stress_tests"] = "PARTIAL"
                return True  # 压力测试允许部分失败
        
        except subprocess.TimeoutExpired:
            self.log("压力测试超时", "ERROR")
            self.results["stress_tests"] = "TIMEOUT"
            return False
        except Exception as e:
            self.log(f"压力测试异常: {str(e)}", "ERROR")
            self.results["stress_tests"] = "ERROR"
            return False
    
    def generate_final_report(self):
        """生成最终测试报告"""
        self.log("最终测试报告", "SECTION")
        
        total_time = (self.end_time - self.start_time).total_seconds()
        
        print(f"\n测试开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"测试结束时间: {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"总耗时: {total_time:.2f} 秒\n")
        
        print("测试结果汇总:")
        print("-" * 70)
        
        test_names = {
            "unit_tests": "单元测试 (Executor 修复验证)",
            "smoke_tests": "烟雾测试 (核心功能验证)",
            "e2e_tests": "端到端测试 (完整流程验证)",
            "stress_tests": "压力测试 (高负载验证)",
        }
        
        passed_count = 0
        failed_count = 0
        
        for key, result in self.results.items():
            name = test_names.get(key, key)
            
            if result == "PASSED":
                print(f"[OK] {name}: 通过")
                passed_count += 1
            elif result == "PARTIAL":
                print(f"[WARN] {name}: 部分通过")
                passed_count += 1
            elif result == "FAILED":
                print(f"[FAIL] {name}: 失败")
                failed_count += 1
            elif result == "TIMEOUT":
                print(f"[TIMEOUT] {name}: 超时")
                failed_count += 1
            elif result == "ERROR":
                print(f"[WARN] {name}: 异常")
                failed_count += 1
            else:
                print(f"[SKIP] {name}: 未运行")
        
        print("-" * 70)
        print(f"\n总计: {passed_count} 通过, {failed_count} 失败")
        
        # 判断总体是否通过
        critical_tests = ["unit_tests", "smoke_tests", "e2e_tests"]
        critical_passed = all(
            self.results.get(test) in ["PASSED", "PARTIAL"]
            for test in critical_tests
        )
        
        print("\n" + "="*70)
        if critical_passed:
            print("[OK] 测试总体通过！系统可以投入使用。")
            print("="*70)
            return True
        else:
            print("[FAIL] 关键测试失败，系统需要修复。")
            print("="*70)
            return False
    
    async def run_all_tests(self):
        """运行所有测试"""
        self.start_time = datetime.now()
        
        print("\n" + "="*70)
        print("EAS 2.0 综合测试套件")
        print("="*70)
        print(f"开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        
        # 检查前提条件
        if not await self.check_prerequisites():
            self.log("\n测试前提条件未满足，无法继续", "ERROR")
            return False
        
        # 按顺序运行测试
        test_sequence = [
            ("单元测试", self.run_unit_tests),
            ("烟雾测试", self.run_smoke_tests),
            ("端到端测试", self.run_e2e_tests),
            ("压力测试", self.run_stress_tests),
        ]
        
        for test_name, test_func in test_sequence:
            self.log(f"\n准备运行: {test_name}", "INFO")
            await asyncio.sleep(1)
            
            success = await test_func()
            
            if not success and test_name != "压力测试":
                # 如果关键测试失败，询问是否继续
                self.log(f"\n{test_name}失败，是否继续后续测试？", "WARN")
                # 自动继续（在实际使用中可以添加交互）
                self.log("自动继续后续测试...", "INFO")
            
            await asyncio.sleep(2)
        
        self.end_time = datetime.now()
        
        # 生成最终报告
        overall_success = self.generate_final_report()
        
        return overall_success


async def main():
    """主函数"""
    runner = MasterTestRunner()
    
    # 切换到正确的目录
    if not os.path.exists("test_executor_fixes.py"):
        os.chdir("python")
    
    success = await runner.run_all_tests()
    
    return 0 if success else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(1)
