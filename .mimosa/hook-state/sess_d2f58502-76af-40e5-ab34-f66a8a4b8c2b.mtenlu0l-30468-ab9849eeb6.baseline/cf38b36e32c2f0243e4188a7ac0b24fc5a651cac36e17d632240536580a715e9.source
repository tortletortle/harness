#!/usr/bin/env python3
"""
快速测试特定模型
"""

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider, Message


async def test_model(llm_client, provider_name, model):
    """测试单个模型"""
    try:
        response = await llm_client.chat(
            provider_name=provider_name,
            messages=[Message(role="user", content="测试")],
            model=model,
            temperature=0.1,
            max_tokens=10
        )
        print(f"✅ {provider_name}/{model} - 可用")
        return True
    except Exception as e:
        print(f"❌ {provider_name}/{model} - {str(e)[:100]}")
        return False


async def main():
    # 加载配置
    with open("config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    llm_client = LLMClient()
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_urls[provider_name])
            )
    
    print("测试目标模型...")
    print()
    
    # 测试用户要求的模型
    await test_model(llm_client, "bbapi", "claude-sonnet-5")
    await test_model(llm_client, "stepfun", "step-3.7")
    await test_model(llm_client, "stepfun", "step-3.7-flash")
    

if __name__ == "__main__":
    asyncio.run(main())
