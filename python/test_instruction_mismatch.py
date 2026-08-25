#!/usr/bin/env python3
"""
核心测试：指令推断与声明不匹配的 bug 验证
通过对比 _infer_instruction() 与工具声明的指令名，验证推断的准确性
"""

from executor import DefaultExecutor
from tools import get_registry

def main():
    print("\n" + "="*70)
    print("测试：指令推断与声明的不匹配")
    print("="*70)
    
    executor = DefaultExecutor(llm_client=None, model_config={})
    registry = executor.tool_registry
    
    # 提取所有工具的声明指令
    print("\n[1] 从工具元数据提取声明指令：\n")
    
    test_tools = [
        "text_editor", "command_runner", "web_fetcher", 
        "file_creator", "git_operator", "package_installer"
    ]
    
    declared_map = {}
    for tool_id in test_tools:
        tool = registry.get_tool(tool_id)
        if tool:
            metadata = tool.get_metadata()
            instructions = [i["name"] for i in metadata["capabilities"]["instructions"]]
            declared_map[tool_id] = instructions
            print(f"  {tool_id:18} => {instructions}")
    
    # 测试推断
    print("\n[2] 测试 _infer_instruction() 的推断结果：\n")
    
    test_cases = [
        ("text_editor", {"path": "test.txt", "content": "hello"}),
        ("text_editor", {"path": "test.txt", "old_text": "a", "new_text": "b"}),
        ("text_editor", {"path": "test.txt"}),
        ("command_runner", {"command": "ls"}),
        ("web_fetcher", {"url": "http://example.com"}),
        ("web_fetcher", {"url": "http://example.com", "path": "/tmp/file"}),
    ]
    
    mismatches = []
    
    for tool_id, inputs in test_cases:
        inferred = executor._infer_instruction(tool_id, inputs)
        declared = declared_map.get(tool_id, [])
        
        in_declared = inferred in declared if inferred else False
        status = "[MATCH]" if in_declared else ("[MISSING]" if inferred is None else "[MISMATCH]")
        
        print(f"  {status:10} {tool_id:15} => {str(inferred):20} (声明: {declared})")
        
        if status == "[MISMATCH]":
            mismatches.append((tool_id, inferred, declared))
    
    # 汇总
    print("\n[3] 汇总结果：\n")
    print(f"  总测试数：{len(test_cases)}")
    print(f"  不匹配数：{len(mismatches)}")
    
    if mismatches:
        print(f"\n  [ALERT] 发现 {len(mismatches)} 个不匹配的推断指令：\n")
        for tool_id, inferred, declared in mismatches:
            print(f"    - {tool_id}")
            print(f"      推断值：'{inferred}'")
            print(f"      声明指令：{declared}")
            
            # 验证推断值是否被工具支持
            tool = registry.get_tool(tool_id)
            if tool and hasattr(tool, 'execute'):
                test_result = tool.execute(inferred, {})
                is_supported = "Invalid instruction" not in test_result.get("message", "")
                support_status = "✓ 工具支持" if is_supported else "✗ 工具不支持"
                print(f"      {support_status}")
            print()

if __name__ == "__main__":
    main()
