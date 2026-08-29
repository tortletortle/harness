#!/usr/bin/env python3
"""
简单测试 - 验证环境和API密钥
"""

import os
import sys

def test_env():
    """检查环境变量"""
    print("🔍 检查配置文件...")
    
    # 检查config/models.yaml
    config_ok = False
    try:
        import yaml
        with open("config/models.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        api_keys = config.get("api_keys", {})
        if api_keys:
            print("✅ config/models.yaml 已加载")
            print(f"   配置了 {len(api_keys)} 个API provider")
            for provider_name in list(api_keys.keys())[:3]:
                print(f"   - {provider_name}")
            if len(api_keys) > 3:
                print(f"   ... (共{len(api_keys)}个)")
            config_ok = True
        else:
            print("❌ config/models.yaml 中没有API密钥")
    except Exception as e:
        print(f"❌ 无法读取config/models.yaml: {e}")
    
    print()
    return config_ok


def test_imports():
    """检查依赖包"""
    print("\n🔍 检查Python依赖...")
    
    packages = {
        "fastapi": "FastAPI",
        "pydantic": "Pydantic",
        "anthropic": "Anthropic SDK",
        "openai": "OpenAI SDK (可选)",
        "yaml": "PyYAML",
        "uvicorn": "Uvicorn",
    }
    
    results = {}
    for package, name in packages.items():
        try:
            __import__(package)
            print(f"✅ {name}")
            results[package] = True
        except ImportError:
            if package == "openai":
                print(f"⚠️  {name} (可选)")
                results[package] = None
            else:
                print(f"❌ {name} 未安装")
                results[package] = False
    
    return all(v for v in results.values() if v is not None)


def main():
    """主函数"""
    print("=" * 60)
    print("EAS 2.0 - 环境检查")
    print("=" * 60)
    print()
    
    env_ok = test_env()
    deps_ok = test_imports()
    
    print("\n" + "=" * 60)
    if env_ok and deps_ok:
        print("✅ 环境配置正确！")
        print("\n下一步:")
        print("  cd python")
        print("  python -m uvicorn api.main:app --host 0.0.0.0 --port 9000")
    else:
        print("⚠️  请修复上述问题")
        if not deps_ok:
            print("\n安装依赖:")
            print("  cd python")
            print("  pip install -r requirements.txt")
        if not env_ok:
            print("\n配置API密钥:")
            print("  编辑 .env 文件，添加 ANTHROPIC_API_KEY")
    print("=" * 60)


if __name__ == "__main__":
    main()
