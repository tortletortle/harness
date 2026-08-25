#!/usr/bin/env python3
"""
测试 2：验证各工具对推断指令的实际支持情况
用具体的 inputs 来测试，确认推断指令是否真的被工具支持
"""

from tools import get_registry

def main():
    print("\n" + "="*70)
    print("测试 2：推断指令的工具支持验证")
    print("="*70)
    
    registry = get_registry()
    
    # 测试用例：(工具 ID, 推断指令, 所需参数)
    test_cases = [
        # text_editor 的推断指令 "write"
        ("text_editor", "write", {
            "path": "/tmp/test.txt",
            "content": "hello world"
        }),
        
        # command_runner 的推断指令 "run"
        ("command_runner", "run", {
            "command": "echo hello"
        }),
        
        # web_fetcher 的推断指令 "fetch"
        ("web_fetcher", "fetch", {
            "url": "http://example.com"
        }),
        
        # 正确的指令名比较：command_runner 用 "run_command"
        ("command_runner", "run_command", {
            "command": "echo hello"
        }),
        
        # 正确的指令名比较：web_fetcher 用 "fetch_content"
        ("web_fetcher", "fetch_content", {
            "url": "http://example.com"
        }),
    ]
    
    print("\n直接测试工具对推断指令的支持：\n")
    
    results = []
    
    for tool_id, instruction, inputs in test_cases:
        tool = registry.get_tool(tool_id)
        if not tool:
            print(f"  [SKIP] {tool_id}:{instruction} - 工具不存在")
            continue
        
        # 执行
        result = tool.execute(instruction, inputs)
        success = result.get("success", False)
        message = result.get("message", "")
        
        # 判断是否因为指令名而失败
        is_invalid = "Invalid instruction" in message or "未知指令" in message
        
        status = "[OK]" if success else ("[INVALID]" if is_invalid else "[FAIL]")
        
        print(f"  {status:10} {tool_id:15}:{instruction:18} | {message[:40]}")
        
        results.append({
            "tool": tool_id,
            "instruction": instruction,
            "success": success,
            "is_invalid": is_invalid,
            "message": message
        })
    
    # 汇总
    print("\n分析结果：\n")
    
    invalid_instructions = [r for r in results if r["is_invalid"]]
    working_instructions = [r for r in results if r["success"]]
    
    print(f"  总测试：{len(results)}")
    print(f"  成功：{len(working_instructions)}")
    print(f"  因指令名失败：{len(invalid_instructions)}")
    
    if invalid_instructions:
        print(f"\n  [ALERT] 以下推断指令根本不被支持（Invalid instruction）：\n")
        for r in invalid_instructions:
            print(f"    - {r['tool']}:{r['instruction']}")
            print(f"      错误：{r['message']}\n")

if __name__ == "__main__":
    main()
