#!/usr/bin/env python3
"""
测试 models.yaml 中配置的所有API连接
"""

import yaml
import requests
import json
from pathlib import Path
from typing import Dict, Any

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def load_config() -> Dict[str, Any]:
    """加载模型配置"""
    config_path = Path(__file__).parent.parent / "config" / "models.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def test_api(provider: str, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """
    测试单个API连接
    返回: (是否成功, 响应消息)
    """
    # 根据不同的provider调整URL
    if 'chat/completions' not in base_url:
        if base_url.endswith('/'):
            url = f"{base_url}v1/chat/completions"
        else:
            url = f"{base_url}/v1/chat/completions" if not base_url.endswith('/v1') else f"{base_url}/chat/completions"
    else:
        url = base_url
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # 构建测试请求
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "你好，请用一句话回复"}
        ],
        "max_tokens": 50,
        "temperature": 0.1
    }
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            return True, f"成功! 响应: {content[:50]}..."
        else:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"
            
    except requests.exceptions.Timeout:
        return False, "请求超时"
    except requests.exceptions.ConnectionError:
        return False, "连接失败"
    except Exception as e:
        return False, f"错误: {str(e)}"

def main():
    print(f"{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}开始测试所有模型API连接{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}\n")
    
    # 加载配置
    config = load_config()
    api_keys = config.get('api_keys', {})
    base_urls = config.get('base_urls', {})
    models_config = config.get('models', {})
    
    # 统计结果
    total = 0
    passed = 0
    failed = 0
    
    # 测试每个provider
    tested_providers = set()
    
    for role, role_config in models_config.items():
        for priority in ['primary', 'fallback']:
            if priority in role_config:
                model_info = role_config[priority]
                provider = model_info['provider']
                model = model_info['model']
                
                # 避免重复测试同一个provider
                if provider in tested_providers:
                    continue
                tested_providers.add(provider)
                
                if provider not in api_keys or provider not in base_urls:
                    print(f"{Colors.YELLOW}⚠ [{provider}] 配置不完整，跳过{Colors.END}")
                    continue
                
                total += 1
                api_key = api_keys[provider]
                base_url = base_urls[provider]
                
                print(f"{Colors.BLUE}测试 {provider.upper()}{Colors.END}")
                print(f"  模型: {model}")
                print(f"  地址: {base_url}")
                
                success, message = test_api(provider, base_url, api_key, model)
                
                if success:
                    passed += 1
                    print(f"{Colors.GREEN}  ✓ {message}{Colors.END}\n")
                else:
                    failed += 1
                    print(f"{Colors.RED}  ✗ {message}{Colors.END}\n")
    
    # 输出汇总
    print(f"{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}测试完成{Colors.END}")
    print(f"  总计: {total}")
    print(f"{Colors.GREEN}  成功: {passed}{Colors.END}")
    print(f"{Colors.RED}  失败: {failed}{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}")
    
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    exit(main())
