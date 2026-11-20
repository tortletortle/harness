import json
import runpy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

probe = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/test_services.py"))
python_identity = "EAS 2.0 API is running"


@pytest.mark.parametrize(("status", "body", "healthy"), [
    (200, json.dumps({"status": "ok", "message": python_identity}), True),
    (500, json.dumps({"status": "ok", "message": python_identity}), False),
    (200, json.dumps({"status": "ok", "service": "eas-rust-server"}), False),
    (200, json.dumps({"status": "degraded", "message": python_identity}), False),
    (200, "<html>frontend</html>", False),
])
def test_health_response(status, body, healthy):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body.encode())

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.handle_request, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        assert probe["_check_health"]("Python API", url, "message", python_identity) is healthy
        worker.join(timeout=5)


@pytest.mark.parametrize(("args", "python_ok", "rust_ok", "code"), [
    (["--python-only"], True, False, 0),
    (["--python-only"], False, True, 1),
    ([], True, False, 1),
    ([], True, True, 0),
])
def test_cli_checks_selected_endpoints(monkeypatch, args, python_ok, rust_ok, code):
    urls = []

    def check(_name, url, _field, _identity):
        urls.append(url)
        return python_ok if len(urls) == 1 else rust_ok

    monkeypatch.setitem(probe["main"].__globals__, "_check_health", check)
    assert probe["main"](args) == code
    expected = ["http://127.0.0.1:8000/"]
    if not args:
        expected.append("http://127.0.0.1:8090/health")
    assert urls == expected
