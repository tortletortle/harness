#!/usr/bin/env python3
"""检查默认本地地址上的 EAS 服务。"""

import argparse
import json
from http.client import HTTPException
from urllib.request import urlopen


def _check_health(name, url, identity_field, identity):
    try:
        with urlopen(url, timeout=5) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("健康端点未直接返回 HTTP 200")
            body = json.load(response)
        if not isinstance(body, dict) or body.get(identity_field) != identity:
            raise ValueError("响应与目标服务不匹配")
        if body.get("status") != "ok":
            raise ValueError("服务未报告 ok")
    except (OSError, HTTPException, ValueError) as error:
        print(f"❌ {name} 检查失败 ({url}): {error}")
        return False
    print(f"✅ {name} 正常 ({url})")
    return True


def test_python_api():
    return _check_health("Python API", "http://127.0.0.1:8000/",
                         "message", "EAS 2.0 API is running")


def test_rust_server():
    return _check_health("Rust Server", "http://127.0.0.1:8090/health",
                         "service", "eas-rust-server")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-only", action="store_true", help="只检查 Python 后端")
    args = parser.parse_args(argv)
    python_ok = test_python_api()
    rust_ok = args.python_only or test_rust_server()
    if not python_ok:
        print("启动 Python 后端：在仓库根目录运行 python scripts/start_api.py")
    else:
        print("Python API 文档：http://127.0.0.1:8000/docs")
    if not rust_ok:
        print("启动 Rust 服务：在仓库根目录运行 cargo run --bin eas-server")
    return 0 if python_ok and rust_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
