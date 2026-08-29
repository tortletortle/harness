#!/usr/bin/env python3
"""
快速测试 API 连接性
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider, Message


async def test_api(provider_name: str, model: str, llm_client: LLMClient):
    """测试单个 API"""
    print(f"\n{'='*60}")
    print(f"测试 {provider_name} / {model}")
    print(f"{'='*60}")
    
    try:
        messages = [Message(role="user", content="请用一句话回复：你好")]
        
        response = await llm_client.chat(
            provider_name=provider_name,
            messages=messages,
            model=model,
            temperature=0.1,
            max_tokens=100,
        )
        
        print(f"✅ 成功")
        print(f"   响应长度: {len(response.content)} 字符")
        print(f"   响应内容: {response.content[:100]}...")
        return True
        
    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    print("="*60)
    print("  API 连接性测试")
    print("="*60)
    
    # 加载配置
    with open("config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 创建 LLM 客户端
    llm_client = LLMClient()
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            print(f"\n注册 {provider_name}: {base_urls[provider_name]}")
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_urls[provider_name])
            )
    
    # 测试关键模型
    tests = [
        ("newapi", "claude-haiku-4-5-20251001"),
        ("bbapi", "claude-opus-5"),
        ("bbapi", "claude-sonnet-5"),
        ("stepfun", "step-3.7-flash"),
    ]
    
    results = {"success": 0, "failed": 0}
    
    for provider_name, model in tests:
        success = await test_api(provider_name, model, llm_client)
        if success:
            results["success"] += 1
        else:
            results["failed"] += 1
    
    # 汇总
    print(f"\n{'='*60}")
    print("测试结果汇总")
    print(f"{'='*60}")
    print(f"成功: {results['success']}/{len(tests)}")
    print(f"失败: {results['failed']}/{len(tests)}")
    
    return results["failed"] == 0


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n👋 用户中断")
        sys.exit(1)
