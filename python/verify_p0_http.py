"""
P0 修复的真实 HTTP 验证：在同一进程内起服务、发请求、关服务。
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = "e2e-verify-token-9988"
BASE = "http://127.0.0.1:8011"
PY = sys.executable

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def _lower(headers):
    """HTTP 头不区分大小写，uvicorn 发小写。统一降为小写再查。"""
    return {k.lower(): v for k, v in headers.items()}


def req(path, token=None, header_name="Authorization", method="GET", origin=None):
    url = BASE + path
    r = urllib.request.Request(url, method=method)
    if token:
        value = f"Bearer {token}" if header_name == "Authorization" else token
        r.add_header(header_name, value)
    if origin:
        r.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), _lower(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _lower(e.headers)
    except Exception as e:
        return 0, f"ERR {e}", {}


env = dict(os.environ)
env["EAS_API_TOKEN"] = TOKEN
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUNBUFFERED"] = "1"

log = open("verify_http_server.log", "w", encoding="utf-8")
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "8011"],
    stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(Path(__file__).parent),
)

try:
    # 等待就绪
    ready = False
    for _ in range(60):
        code, _, _ = req("/")
        if code == 200:
            ready = True
            break
        time.sleep(1)
    check("服务启动成功", ready)

    if ready:
        code, body, _ = req("/")
        check("健康探针 / 免认证返回 200", code == 200, f"code={code}")

        code, body, hdrs = req("/api/status")
        check("无 token 访问 /api/status 返回 401", code == 401, f"code={code}")
        check("401 带 WWW-Authenticate 头",
              "bearer" in hdrs.get("www-authenticate", "").lower(),
              hdrs.get("www-authenticate", "<缺失>"))

        code, _, _ = req("/api/status", token="wrong-token")
        check("错误 token 返回 401", code == 401, f"code={code}")

        code, body, _ = req("/api/status", token=TOKEN)
        check("正确 Bearer token 返回 200", code == 200, f"code={code}")
        check("响应含 status 字段", '"status"' in body, body[:80])

        code, _, _ = req("/api/status", token=TOKEN, header_name="X-API-Token")
        check("X-API-Token 头返回 200", code == 200, f"code={code}")

        # 所有敏感端点都必须要求认证
        for path in ["/api/designs", "/api/plans", "/api/tools", "/api/config", "/api/status"]:
            code, _, _ = req(path)
            check(f"{path} 无 token 被拒", code == 401, f"code={code}")

        for path, m in [("/api/design", "POST"), ("/api/config", "POST"),
                        ("/api/restart", "POST"), ("/api/chat", "POST")]:
            code, _, _ = req(path, method=m)
            check(f"{m} {path} 无 token 被拒", code == 401, f"code={code}")

        # CORS：不应再回显任意 Origin
        code, body, hdrs = req("/api/status", token=TOKEN, origin="http://evil.example.com")
        acao = hdrs.get("access-control-allow-origin")
        check("恶意 Origin 不被放行", acao != "http://evil.example.com" and acao != "*",
              f"ACAO={acao!r}")

        code, body, hdrs = req("/api/status", token=TOKEN, origin="http://localhost:5173")
        acao = hdrs.get("access-control-allow-origin")
        check("本地前端 Origin 被放行", acao == "http://localhost:5173", f"ACAO={acao!r}")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
print("\n" + "=" * 56)
print(f"总计 {total}，通过 {passed}，失败 {total - passed}")
if passed < total:
    print("\n失败项：")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name} {detail}")
print("=" * 56)
sys.exit(0 if passed == total else 1)
