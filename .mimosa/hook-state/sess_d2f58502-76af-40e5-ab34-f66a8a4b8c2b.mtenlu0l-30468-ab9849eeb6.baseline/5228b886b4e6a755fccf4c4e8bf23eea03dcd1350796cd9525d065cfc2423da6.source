#!/usr/bin/env python3
"""
快速测试脚本 - 验证API密钥和基础功能
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


async def test_anthropic():
    """测试Anthropic API"""
    print("🧪 测试Anthropic API...")
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 未设置")
        return False
    
    try:
        from llm_client import AnthropicProvider, Message
        
        provider = AnthropicProvider(api_key)
        
        # 简单的测试调用
        response = await provider.chat(
            messages=[Message(role="user", content="Say hello")],
            model="claude-3-haiku-20240307",
            max_tokens=50,
        )
        
        print(f"✅ Anthropic API 正常")
        print(f"   Response: {response.content[:100]}...")
        return True
        
    except Exception as e:
        print(f"❌ Anthropic API 错误: {e}")
        return False


async def test_openai():
    """测试OpenAI API"""
    print("\n🧪 测试OpenAI API...")
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️  OPENAI_API_KEY 未设置（可选）")
        return None
    
    try:
        from llm_client import OpenAIProvider, Message
        
        provider = OpenAIProvider(api_key)
        
        # 简单的测试调用
        response = await provider.chat(
            messages=[Message(role="user", content="Say hello")],
            model="gpt-3.5-turbo",
            max_tokens=50,
        )
        
        print(f"✅ OpenAI API 正常")
        print(f"   Response: {response.content[:100]}...")
        return True
        
    except Exception as e:
        print(f"❌ OpenAI API 错误: {e}")
        return False


async def test_config():
    """测试配置文件"""
    print("\n🧪 测试配置文件...")
    
    try:
        import yaml
        
        config_path = Path(__file__).parent.parent / "config" / "models.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        print(f"✅ 配置文件加载成功")
        print(f"   模型配置: {list(config['models'].keys())}")
        return True
        
    except Exception as e:
        print(f"❌ 配置文件错误: {e}")
        return False


async def main():
    """主测试流程"""
    print("=" * 60)
    print("EAS 2.0 - 快速验证测试")
    print("=" * 60)
    
    results = {}
    
    # 测试配置
    results['config'] = await test_config()
    
    # 测试Anthropic
    results['anthropic'] = await test_anthropic()
    
    # 测试OpenAI（可选）
    results['openai'] = await test_openai()
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结:")
    print("=" * 60)
    
    for name, result in results.items():
        if result is True:
            print(f"✅ {name}: 通过")
        elif result is False:
            print(f"❌ {name}: 失败")
        else:
            print(f"⚠️  {name}: 跳过")
    
    # 检查是否可以启动服务
    if results['config'] and results['anthropic']:
        print("\n🚀 配置正确！可以启动服务了")
        print("\n下一步:")
        print("  1. 启动Python API: cd python && python -m uvicorn api.main:app --port 9000")
        print("  2. 启动Rust Server: cargo run --bin eas-server")
        return 0
    else:
        print("\n⚠️  请先修复上述错误")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
