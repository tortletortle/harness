"""
pytest 全局配置

python/ 目录下的模块互相之间使用顶层导入（例如 `from models import ...`，
不是 `from python.models import ...`），因为它们原本被设计为
`sys.path` 已包含 `python/` 目录时运行（参考 api/server.py 的
`sys.path.insert(0, str(Path(__file__).parent.parent))`）。

为了让 tests/ 下的测试可以直接 `from designer import DefaultDesigner`
而不必逐个文件手写 sys.path hack，这里在会话级别统一插入路径。
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest

_PYTHON_DIR = Path(__file__).resolve().parent.parent / "python"
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True, scope="session")
def isolated_workspace_root(tmp_path_factory):
    """默认把工作区根指向会话级临时目录，测试不写仓库根。

    背景：计划执行收尾会调用 `tools.project_memory.save_entry()` 落
    `PROJECT_MEMORY.md`。未隔离时，跑一次套件就会把合成的「执行摘要」写进
    仓库根的真实记忆文件（2026-09-19 实测：`test_plan_lifecycle_semantics.py`
    单次运行追加 3 条，773 字节）。已有用例仍可用
    `monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))` 覆盖成各自目录。
    """
    if os.environ.get("EAS_WORKSPACE_ROOT"):
        # 外部测试包装器已指定隔离根（docs/AGENT_HARNESS_DESIGN.md 记录的做法），尊重它。
        yield os.environ["EAS_WORKSPACE_ROOT"]
        return
    root = tmp_path_factory.mktemp("eas_workspace_root")
    os.environ["EAS_WORKSPACE_ROOT"] = str(root)
    try:
        yield str(root)
    finally:
        os.environ.pop("EAS_WORKSPACE_ROOT", None)


def _repo_memory_digest() -> str:
    memory = _REPO_ROOT / "PROJECT_MEMORY.md"
    if not memory.exists():
        return "<missing>"
    return hashlib.sha256(memory.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def no_repo_project_memory_writes(request):
    """护栏：任何用例都不许改写仓库根的 PROJECT_MEMORY.md。"""
    before = _repo_memory_digest()
    yield
    after = _repo_memory_digest()
    assert before == after, (
        f"{request.node.nodeid} 改写了仓库根 PROJECT_MEMORY.md（记忆文件被测试污染）。"
        "请在该用例 fixture 里 monkeypatch.setenv('EAS_WORKSPACE_ROOT', str(tmp_path))，"
        "或让被测路径使用执行级工作区覆盖（tools.workspace.set_workspace_override）。"
    )


def pytest_addoption(parser):
    parser.addoption("--live-models", action="store_true", default=False,
                     help="Explicitly allow real external model HTTP calls (may incur cost).")


@pytest.fixture(autouse=True)
def no_unintended_live_models(request, monkeypatch):
    """Offline-by-default guard; mocked transports and local API tests still work."""
    if request.config.getoption("--live-models"):
        return
    import httpx
    original = httpx.AsyncClient.send

    async def guarded(client, req, *args, **kwargs):
        model_path = any(p in req.url.path for p in ("/chat/completions", "/messages", "/responses"))
        mocked = isinstance(client._transport, httpx.MockTransport)
        local = req.url.host in {"localhost", "127.0.0.1", "::1", "testserver"}
        if model_path and not mocked and not local:
            raise AssertionError("Real model HTTP disabled in pytest; use --live-models for explicit acceptance runs")
        return await original(client, req, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded)
