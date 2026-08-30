#!/usr/bin/env python3
"""
测试所有API提供商
确保每个API都能正常响应
"""

import asyncio
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider, Message


async def test_provider(llm_client: LLMClient, provider_name: str, model: str):
    """测试单个provider"""
    print(f"\n{'='*70}")
    print(f"测试: {provider_name} - {model}")
    print(f"{'='*70}")
    
    try:
        # 发送简单的测试消息
        response = await llm_client.chat(
            provider_name=provider_name,
            messages=[Message(role="user", content="你好，请回复'测试成功'")],
            model=model,
            temperature=0.1,
            max_tokens=100
        )
        
        print(f"✅ 成功!")
        print(f"   模型: {response.model}")
        print(f"   回复: {response.content[:100]}")
        if response.usage:
            print(f"   用量: {response.usage}")
        
        return True
        
    except Exception as e:
        print(f"❌ 失败!")
        print(f"   错误: {str(e)}")
        return False


async def main():
    """主函数"""
    print("="*70)
    print("  EAS 2.0 - API提供商测试工具")
    print("="*70)
    print()
    
    # 加载配置
    try:
        with open("config/models.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"❌ 无法加载配置文件: {e}")
        return
    
    # 创建LLM客户端
    llm_client = LLMClient()
    
    # 注册所有provider
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    print("📋 注册API提供商...")
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            base_url = base_urls[provider_name]
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_url)
            )
            print(f"  ✓ {provider_name}: {base_url}")
    
    print()
    print("="*70)
    print("开始测试所有API提供商")
    print("="*70)
    
    # 定义测试用例 - 每个provider测试一个常用模型
    test_cases = [
        # newapi
        ("newapi", "claude-opus-5"),
        ("newapi", "claude-sonnet-4"),
        ("newapi", "claude-haiku-4-5-20251001"),
        
        # bbapi
        ("bbapi", "claude-opus-5"),
        ("bbapi", "claude-sonnet-4"),
        
        # hejuapi
        ("hejuapi", "gpt-5.6-sol"),
        ("hejuapi", "gpt-5.6-terra"),
        
        # stepfun
        ("stepfun", "step-3.5-flash"),
        ("stepfun", "step-2x-large"),
    ]
    
    results = {}
    
    for provider_name, model in test_cases:
        success = await test_provider(llm_client, provider_name, model)
        key = f"{provider_name}/{model}"
        results[key] = success
        
        # 短暂延迟避免速率限制
        await asyncio.sleep(1)
    
    # 汇总结果
    print()
    print("="*70)
    print("测试结果汇总")
    print("="*70)
    print()
    
    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    
    print(f"总计: {success_count}/{total_count} 通过")
    print()
    
    # 按provider分组显示
    providers = {}
    for key, success in results.items():
        provider = key.split("/")[0]
        if provider not in providers:
            providers[provider] = []
        providers[provider].append((key, success))
    
    for provider, tests in providers.items():
        passed = sum(1 for _, s in tests if s)
        total = len(tests)
        status = "✅" if passed == total else "⚠️" if passed > 0 else "❌"
        print(f"{status} {provider}: {passed}/{total} 通过")
        
        for test_key, success in tests:
            model = test_key.split("/")[1]
            icon = "  ✓" if success else "  ✗"
            print(f"{icon} {model}")
        print()
    
    # 推荐配置
    print("="*70)
    print("推荐配置")
    print("="*70)
    print()
    
    # 找出完全可用的provider
    working_providers = []
    for provider, tests in providers.items():
        if any(s for _, s in tests):
            # 找出该provider中第一个成功的模型
            for test_key, success in tests:
                if success:
                    model = test_key.split("/")[1]
                    working_providers.append((provider, model))
                    break
    
    if working_providers:
        print("以下配置已验证可用:")
        print()
        for provider, model in working_providers:
            print(f"  provider: {provider}")
            print(f"  model: {model}")
            print()
    else:
        print("❌ 没有找到可用的API提供商")
        print()
        print("请检查:")
        print("  1. API密钥是否有效")
        print("  2. 网络连接是否正常")
        print("  3. API端点是否正确")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print()
        print("👋 用户中断，再见！")
