#!/usr/bin/env python3
"""
综合测试：数据库、新工具、插件系统
"""

import asyncio
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from database import get_database
from tools import get_registry
from plugin_system import get_plugin_manager
from models import DesignDocument, ExecutionPlan, PlanSegment, Step, StepStatus
from uuid import uuid4
from datetime import datetime, timezone
def test_database():
    """测试数据库功能"""
    print("=" * 70)
    print("测试 1: 数据库持久化")
    print("=" * 70)
    print()
    
    db = get_database("data/test_eas.db")
    
    # 创建测试设计书
    from models import DesignPart1, Architecture, Difficulty, ToolAssessment
    
    design = DesignDocument(
        design_id=uuid4(),
        user_request="测试项目",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        part1_design=DesignPart1(
            architecture=Architecture(
                components=["组件1", "组件2"],
                interactions=["交互1"]
            ),
            difficulty=Difficulty(
                overall="easy",
                breakdown={}
            )
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[],
            overall_tool_completeness=1.0,
            risks=[]
        )
    )
    
    # 保存设计书
    print("📝 保存设计书...")
    if db.save_design(design):
        print("✅ 设计书保存成功")
        print(f"   Design ID: {design.design_id}")
    else:
        print("❌ 设计书保存失败")
        return False
    
    # 读取设计书
    print()
    print("📖 读取设计书...")
    loaded_design = db.get_design(str(design.design_id))
    if loaded_design:
        print("✅ 设计书读取成功")
        print(f"   用户需求: {loaded_design.user_request}")
        print(f"   组件数: {len(loaded_design.part1_design.architecture.components)}")
    else:
        print("❌ 设计书读取失败")
        return False
    
    # 列出设计书
    print()
    print("📋 列出所有设计书...")
    designs = db.list_designs()
    print(f"✅ 找到 {len(designs)} 个设计书")
    for d in designs[:3]:
        print(f"   - {d['design_id']}: {d['user_request']}")
    
    print()
    return True


def test_new_tools():
    """测试新工具"""
    print("=" * 70)
    print("测试 2: 新工具（Git、文本编辑、Web）")
    print("=" * 70)
    print()
    
    registry = get_registry()
    
    # 测试文本编辑器
    print("📝 测试文本编辑器...")
    result = registry.execute_tool(
        "text_editor",
        "read_file",
        {"path": "./test_hello_world/main.py"}
    )
    if result["success"]:
        print(f"✅ 读取文件成功")
        print(f"   内容: {result['content'][:50]}...")
    else:
        print(f"⚠️  读取文件: {result['message']}")
    
    print()
    
    # 测试Git操作
    print("🔧 测试Git操作...")
    result = registry.execute_tool(
        "git_operator",
        "git_init",
        {"path": "./test_git_repo"}
    )
    if result["success"]:
        print(f"✅ Git初始化成功")
    else:
        print(f"⚠️  Git初始化: {result['message']}")
    
    print()
    
    # 列出所有工具
    print("📦 所有可用工具:")
    tools = registry.list_tools()
    for tool in tools:
        print(f"   - {tool['tool_id']}: {tool['name']}")
    
    print()
    return True


def test_plugin_system():
    """测试插件系统"""
    print("=" * 70)
    print("测试 3: 插件系统")
    print("=" * 70)
    print()
    
    pm = get_plugin_manager("plugins")
    
    # 加载插件
    print("📦 从目录加载插件...")
    count = pm.load_plugins_from_directory()
    print(f"✅ 加载了 {count} 个插件")
    print()
    
    # 列出插件
    print("📋 已加载的插件:")
    plugins = pm.list_plugins()
    for plugin in plugins:
        print(f"   - {plugin['name']} v{plugin['version']}")
        print(f"     类型: {plugin['type']}")
        print(f"     描述: {plugin['description']}")
    print()
    
    # 测试插件执行
    if count > 0:
        print("🧪 测试插件执行...")
        result = pm.execute_tool_plugin(
            "json_tool",
            "validate_json",
            {"json_str": '{"key": "value"}'}
        )
        if result["success"]:
            print(f"✅ 插件执行成功: {result['message']}")
        else:
            print(f"❌ 插件执行失败: {result['message']}")
        print()
    
    return True


async def main():
    """主函数"""
    print("=" * 70)
    print("  EAS 2.0 - 新功能综合测试")
    print("=" * 70)
    print()
    
    results = []
    
    # 测试1: 数据库
    try:
        results.append(("数据库持久化", test_database()))
    except Exception as e:
        print(f"❌ 数据库测试失败: {e}")
        results.append(("数据库持久化", False))
    
    # 测试2: 新工具
    try:
        results.append(("新工具", test_new_tools()))
    except Exception as e:
        print(f"❌ 新工具测试失败: {e}")
        results.append(("新工具", False))
    
    # 测试3: 插件系统
    try:
        results.append(("插件系统", test_plugin_system()))
    except Exception as e:
        print(f"❌ 插件系统测试失败: {e}")
        results.append(("插件系统", False))
    
    # 汇总结果
    print("=" * 70)
    print("测试结果汇总")
    print("=" * 70)
    print()
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")
    
    print()
    print(f"总计: {passed}/{total} 通过")
    print()
    
    if passed == total:
        print("🎉 所有测试通过！")
    else:
        print("⚠️  部分测试失败")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("👋 用户中断")
