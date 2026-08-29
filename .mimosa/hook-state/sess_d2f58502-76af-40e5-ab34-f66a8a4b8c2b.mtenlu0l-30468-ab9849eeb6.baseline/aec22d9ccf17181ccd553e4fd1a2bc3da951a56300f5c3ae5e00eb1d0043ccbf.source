#!/usr/bin/env python3
"""
测试服务是否正常运行
"""

import requests
import json

def test_python_api():
    """测试Python API"""
    try:
        response = requests.get("http://localhost:9000/health", timeout=5)
        print("✅ Python API (9000) 正常")
        print(f"   响应: {response.json()}")
        return True
    except Exception as e:
        print(f"❌ Python API (9000) 失败: {e}")
        return False


def test_rust_server():
    """测试Rust Server"""
    try:
        response = requests.get("http://localhost:8090/health", timeout=5)
        print("✅ Rust Server (8090) 正常")
        print(f"   响应: {response.json()}")
        return True
    except Exception as e:
        print(f"❌ Rust Server (8090) 失败: {e}")
        return False


def main():
    print("=" * 60)
    print("EAS 2.0 - 服务测试")
    print("=" * 60)
    print()
    
    python_ok = test_python_api()
    print()
    rust_ok = test_rust_server()
    
    print()
    print("=" * 60)
    if python_ok and rust_ok:
        print("🎉 所有服务运行正常！")
        print("\n下一步:")
        print("  - 访问 http://localhost:9000/docs 查看Python API文档")
        print("  - 测试AI功能")
    else:
        print("⚠️  部分服务未运行")
        if not python_ok:
            print("\n启动Python服务:")
            print("  cd python")
            print("  python -m uvicorn api.main:app --host 0.0.0.0 --port 9000")
        if not rust_ok:
            print("\n启动Rust服务:")
            print("  cargo run --bin eas-server")
    print("=" * 60)


if __name__ == "__main__":
    main()
