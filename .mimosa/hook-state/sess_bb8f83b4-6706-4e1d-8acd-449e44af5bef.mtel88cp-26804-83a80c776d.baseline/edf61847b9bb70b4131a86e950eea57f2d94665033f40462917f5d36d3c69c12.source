#!/usr/bin/env python3
"""
测试 JSON 修复工具
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from utils.json_fixer import JSONFixer, fix_and_validate_plan_json


def test_json_fixer():
    """测试 JSON 修复功能"""
    print("=" * 70)
    print("  JSON 修复工具测试")
    print("=" * 70)
    print()
    
    test_cases = [
        {
            "name": "未闭合的字符串",
            "input": '''{
  "segments": [{
    "description": "初始化项目,
    "steps": []
  }]
}''',
            "should_fix": True
        },
        {
            "name": "尾部逗号",
            "input": '''{
  "segments": [{
    "description": "初始化",
    "steps": [],
  }],
  "implementation_list": ["main.py",]
}''',
            "should_fix": True
        },
        {
            "name": "未闭合的括号",
            "input": '''{
  "segments": [{
    "description": "初始化",
    "steps": []
  }
}''',
            "should_fix": True
        },
        {
            "name": "单引号",
            "input": """{'segments': [{'description': 'test', 'steps': []}]}""",
            "should_fix": True
        },
        {
            "name": "正确的 JSON",
            "input": '''{
  "segments": [{
    "description": "初始化",
    "steps": []
  }],
  "implementation_list": []
}''',
            "should_fix": True
        },
        {
            "name": "带 markdown 标记",
            "input": '''```json
{
  "segments": [{
    "description": "初始化",
    "steps": []
  }],
  "implementation_list": []
}
```''',
            "should_fix": True
        }
    ]
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(test_cases, 1):
        print(f"测试 {i}: {test['name']}")
        print(f"输入: {test['input'][:50]}...")
        
        result, error = JSONFixer.fix_json(test['input'])
        
        if test['should_fix']:
            if result is not None:
                print(f"✅ 成功修复")
                passed += 1
            else:
                print(f"❌ 修复失败: {error}")
                failed += 1
        else:
            if result is None:
                print(f"✅ 正确拒绝")
                passed += 1
            else:
                print(f"❌ 不应修复成功")
                failed += 1
        
        print()
    
    print("=" * 70)
    print(f"总计: {passed}/{passed + failed} 通过")
    print("=" * 70)
    
    return passed == (passed + failed)


def test_plan_validation():
    """测试执行计划验证"""
    print("\n" + "=" * 70)
    print("  执行计划结构验证测试")
    print("=" * 70)
    print()
    
    test_cases = [
        {
            "name": "完整的计划",
            "data": {
                "segments": [{
                    "description": "初始化",
                    "steps": [{
                        "description": "创建目录",
                        "tool": "file_creator",
                        "inputs": {"path": "./project"}
                    }]
                }],
                "implementation_list": []
            },
            "should_pass": True
        },
        {
            "name": "缺少 segments",
            "data": {
                "implementation_list": []
            },
            "should_pass": False
        },
        {
            "name": "空 segments",
            "data": {
                "segments": [],
                "implementation_list": []
            },
            "should_pass": False
        },
        {
            "name": "步骤缺少 tool",
            "data": {
                "segments": [{
                    "description": "初始化",
                    "steps": [{
                        "description": "创建目录",
                        "inputs": {}
                    }]
                }]
            },
            "should_pass": False
        }
    ]
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(test_cases, 1):
        print(f"测试 {i}: {test['name']}")
        
        valid, error = JSONFixer.validate_plan_structure(test['data'])
        
        if test['should_pass']:
            if valid:
                print(f"✅ 验证通过")
                passed += 1
            else:
                print(f"❌ 不应失败: {error}")
                failed += 1
        else:
            if not valid:
                print(f"✅ 正确拒绝: {error}")
                passed += 1
            else:
                print(f"❌ 应该失败但通过了")
                failed += 1
        
        print()
    
    print("=" * 70)
    print(f"总计: {passed}/{passed + failed} 通过")
    print("=" * 70)
    
    return passed == (passed + failed)


if __name__ == "__main__":
    success1 = test_json_fixer()
    success2 = test_plan_validation()
    
    if success1 and success2:
        print("\n🎉 所有测试通过！")
        sys.exit(0)
    else:
        print("\n❌ 部分测试失败")
        sys.exit(1)
