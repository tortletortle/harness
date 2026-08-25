#!/usr/bin/env python3
"""
测试 3：端到端执行流程 - 观察推断指令在实际计划中的表现
直接调用 executor.execute_step()，模拟步骤执行
"""

import asyncio
import json
from uuid import uuid4
from models import Step, StepStatus
from executor import DefaultExecutor
from llm_client import LLMClient

async def main():
    print("\n" + "="*70)
    print("测试 3：端到端步骤执行（使用推断指令）")
    print("="*70)
    
    executor = DefaultExecutor(llm_client=None, model_config={})
    
    # 定义三个步骤，分别使用会导致推断的工具
    steps = [
        Step(
            step_id=uuid4(),
            segment_id="seg_1",
            order=1,
            description="创建文本文件",
            tool="text_editor",  # 不带指令，让 executor 推断
            inputs={"path": "/tmp/test_1.txt", "content": "hello"},
            check_criteria="文件创建成功",
            outputs=None,
            status=StepStatus.PENDING,
            executed_at=None
        ),
        Step(
            step_id=uuid4(),
            segment_id="seg_1",
            order=2,
            description="执行命令",
            tool="command_runner",  # 不带指令，让 executor 推断
            inputs={"command": "echo test > /tmp/test_2.txt"},
            check_criteria="命令执行成功",
            outputs=None,
            status=StepStatus.PENDING,
            executed_at=None
        ),
        Step(
            step_id=uuid4(),
            segment_id="seg_1",
            order=3,
            description="获取网页内容",
            tool="web_fetcher",  # 不带指令，让 executor 推断
            inputs={"url": "http://example.com"},
            check_criteria="获取内容成功",
            outputs=None,
            status=StepStatus.PENDING,
            executed_at=None
        ),
    ]
    
    print("\n执行步骤并观察结果：\n")
    
    results = []
    
    for step in steps:
        print(f"[执行] 步骤 {step.order}: {step.description}")
        print(f"  工具：{step.tool}")
        print(f"  输入：{step.inputs}")
        
        result = await executor.execute_step(step)
        
        success = result.get("success", False)
        error = result.get("error", "")
        
        status = "[SUCCESS]" if success else "[FAILED]"
        print(f"  {status} 错误: {error if error else '无'}")
        
        results.append({
            "step_id": step.step_id,
            "order": step.order,
            "description": step.description,
            "tool": step.tool,
            "success": success,
            "error": error
        })
        print()
    
    # 汇总
    print("\n"+"="*70)
    print("执行结果汇总")
    print("="*70)
    
    success_count = sum(1 for r in results if r["success"])
    failed_count = len(results) - success_count
    
    print(f"\n  总步骤：{len(results)}")
    print(f"  成功：{success_count}")
    print(f"  失败：{failed_count}")
    
    if failed_count > 0:
        print(f"\n  失败的步骤：\n")
        for r in results:
            if not r["success"]:
                print(f"    - {r['description']} ({r['tool']})")
                print(f"      错误：{r['error']}\n")

if __name__ == "__main__":
    asyncio.run(main())
