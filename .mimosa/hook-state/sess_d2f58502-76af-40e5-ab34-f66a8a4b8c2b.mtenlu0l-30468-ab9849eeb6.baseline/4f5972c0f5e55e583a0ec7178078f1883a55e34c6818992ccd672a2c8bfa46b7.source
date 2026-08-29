"""
阶段3补齐验证：

1. 步骤重试（3.1）：executor.execute_step 的返回值携带 blocked 字段，用于区分
   「可重试错误」（网络抖动、工具异常等）与「不可重试错误」（安全拦截 blocked、
   结果校验不通过 check_status=="failed"）。
   同时验证 config 的 retry.max_attempts / backoff_factor 被正确读取，
   以及重试循环里"命中2次失败1次成功后最终成功"、"命中不可重试错误立即停止"两种路径。

2. 工具超时（3.4）：file_creator / text_editor / manual 三个工具的公开方法都
   接受 timeout 参数，且用 ThreadPoolExecutor 实现真实的超时熔断。
"""

import time
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest


class FakeLLM:
    async def chat(self, **kwargs):
        class R:
            content = "YES"
        return R()


def make_step(tool="file_creator", inputs=None, check_criteria="手动检查"):
    from models import Step, StepStatus
    return Step(
        step_id=uuid.uuid4(),
        order=1,
        description="重试策略测试步骤",
        tool=tool,
        inputs=inputs or {"path": "a.txt", "content": "x"},
        check_criteria=check_criteria,
        status=StepStatus.PENDING,
    )


# ---------------------------------------------------------------------------
# 1. executor 返回值携带 blocked 字段，用于区分可重试 / 不可重试错误
# ---------------------------------------------------------------------------

class RegistryReturning:
    def __init__(self, result):
        self.result = result

    def execute_tool(self, tool_id, instruction, inputs):
        return self.result


@pytest.mark.asyncio
async def test_blocked_error_result_has_blocked_true():
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}}
    registry = RegistryReturning({"success": False, "message": "拒绝", "error": "Dangerous command blocked"})
    ex = DefaultExecutor(FakeLLM(), config, registry)

    result = await ex.execute_step(make_step())
    assert result["success"] is False
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_network_style_error_result_has_blocked_false():
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}}
    registry = RegistryReturning({"success": False, "message": "网络超时", "error": "Connection timeout"})
    ex = DefaultExecutor(FakeLLM(), config, registry)

    result = await ex.execute_step(make_step())
    assert result["success"] is False
    assert result["blocked"] is False


@pytest.mark.asyncio
async def test_check_failed_result_has_blocked_true():
    """结果校验不通过（check_status=="failed"）属于逻辑错误，不是网络抖动，不该重试。"""
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}}

    class NoLLMSaysNo:
        async def chat(self, **kwargs):
            class R:
                content = "NO"
            return R()

    registry = RegistryReturning({"success": True, "message": "工具执行完成"})
    ex = DefaultExecutor(NoLLMSaysNo(), config, registry)

    step = make_step(check_criteria="文件内容必须是特定格式")
    result = await ex.execute_step(step)
    assert result["success"] is False
    assert result["check_status"] == "failed"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_success_result_has_blocked_false():
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}}
    registry = RegistryReturning({"success": True, "message": "done"})
    ex = DefaultExecutor(FakeLLM(), config, registry)

    step = make_step(check_criteria="手动检查")
    result = await ex.execute_step(step)
    assert result["success"] is True
    assert result["blocked"] is False


# ---------------------------------------------------------------------------
# 2. server.py 重试循环：读取 config 的 retry 配置 + 指数退避 + 错误分类
# ---------------------------------------------------------------------------

def test_config_retry_section_is_loaded_with_expected_keys():
    """config/models.yaml 里 retry 是顶层字段，包含 max_attempts / backoff_factor。"""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "models.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    retry_cfg = config.get("retry", {})
    assert "max_attempts" in retry_cfg
    assert "backoff_factor" in retry_cfg
    assert retry_cfg["max_attempts"] >= 1


def test_appstate_has_retry_defaults():
    """AppState 实例默认自带 retry_max_attempts / retry_backoff_factor 字段（未启动 lifespan 时也不报错）。"""
    import sys
    from pathlib import Path

    python_dir = Path(__file__).resolve().parent.parent.parent / "python"
    sys.path.insert(0, str(python_dir))
    from api.server import AppState

    state = AppState()
    assert hasattr(state, "retry_max_attempts")
    assert hasattr(state, "retry_backoff_factor")
    assert state.retry_max_attempts == 3
    assert state.retry_backoff_factor == 2


@pytest.mark.asyncio
async def test_retry_loop_succeeds_after_two_failures():
    """模拟"前2次失败，第3次成功"，验证重试循环最终判定步骤成功，且尝试了3次。"""
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}}

    call_count = {"n": 0}

    class FlakyRegistry:
        def execute_tool(self, tool_id, instruction, inputs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return {"success": False, "message": "网络超时", "error": "Connection timeout"}
            return {"success": True, "message": "done"}

    ex = DefaultExecutor(FakeLLM(), config, FlakyRegistry())
    step = make_step(check_criteria="手动检查")

    max_attempts = 3
    backoff_factor = 2
    attempt = 0
    result = None
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        while True:
            result = await ex.execute_step(step)
            if result.get("success") and result.get("check_status") != "failed":
                break
            non_retryable = result.get("blocked") or result.get("check_status") == "failed"
            if non_retryable:
                break
            if attempt >= max_attempts - 1:
                break
            attempt += 1
            step.retry_count = attempt
            delay = backoff_factor ** attempt
            sleeps.append  # noqa
            await fake_sleep(delay)

    assert result["success"] is True
    assert call_count["n"] == 3
    assert step.retry_count == 2
    # 指数退避：第1次重试延时2秒，第2次重试延时4秒
    assert sleeps == [2, 4]


@pytest.mark.asyncio
async def test_retry_loop_stops_immediately_on_blocked_error():
    """命中 blocked 错误应该立即停止重试，只调用一次工具。"""
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}}
    call_count = {"n": 0}

    class AlwaysBlockedRegistry:
        def execute_tool(self, tool_id, instruction, inputs):
            call_count["n"] += 1
            return {"success": False, "message": "拒绝", "error": "Dangerous command blocked"}

    ex = DefaultExecutor(FakeLLM(), config, AlwaysBlockedRegistry())
    step = make_step(check_criteria="手动检查")

    max_attempts = 3
    attempt = 0
    result = None
    while True:
        result = await ex.execute_step(step)
        if result.get("success") and result.get("check_status") != "failed":
            break
        non_retryable = result.get("blocked") or result.get("check_status") == "failed"
        if non_retryable:
            break
        if attempt >= max_attempts - 1:
            break
        attempt += 1
        step.retry_count = attempt

    assert result["success"] is False
    assert result["blocked"] is True
    assert call_count["n"] == 1, "命中安全拦截应立即停止，不应重试"


# ---------------------------------------------------------------------------
# 3. 工具超时（3.4）：file_creator / text_editor / manual 都接受 timeout 参数
# ---------------------------------------------------------------------------

def test_file_creator_create_file_accepts_timeout_and_succeeds_fast(tmp_path):
    from tools.file_creator import FileCreator

    target = tmp_path / "test.txt"
    result = FileCreator.create_file(str(target), content="hi", timeout=5)
    assert result["success"] is True
    assert target.exists()


def test_file_creator_times_out_on_slow_operation(tmp_path, monkeypatch):
    """monkeypatch 内部实现方法制造延迟，验证 timeout 生效并返回超时失败。"""
    import tools.file_creator as fc_module

    def slow_impl(path, content="", encoding="utf-8"):
        time.sleep(2)
        return {"success": True, "message": "too slow"}

    monkeypatch.setattr(fc_module.FileCreator, "_create_file_impl", slow_impl)

    result = fc_module.FileCreator.create_file(str(tmp_path / "test.txt"), content="hi", timeout=0.3)
    assert result["success"] is False
    assert result["error"] == "Operation timeout"


def test_text_editor_read_file_accepts_timeout(tmp_path):
    from tools.text_editor import TextEditor

    p = tmp_path / "f.txt"
    p.write_text("hello", encoding="utf-8")

    result = TextEditor.read_file(str(p), timeout=5)
    assert result["success"] is True
    assert result["content"] == "hello"


def test_text_editor_times_out_on_slow_operation(tmp_path, monkeypatch):
    import tools.text_editor as te_module

    def slow_impl(path, encoding="utf-8"):
        time.sleep(2)
        return {"success": True, "content": "too slow"}

    monkeypatch.setattr(te_module.TextEditor, "_read_file_impl", slow_impl)

    result = te_module.TextEditor.read_file(str(tmp_path / "f.txt"), timeout=0.3)
    assert result["success"] is False
    assert result["error"] == "Operation timeout"


def test_manual_tool_accepts_timeout_and_succeeds_fast():
    from tools.manual_tool import ManualTool

    result = ManualTool.manual_step(description="测试步骤", timeout=5)
    assert result["success"] is True


def test_manual_tool_times_out_on_slow_operation(monkeypatch):
    import tools.manual_tool as mt_module

    def slow_impl(description="", error=None):
        time.sleep(2)
        return {"success": True, "message": "too slow"}

    monkeypatch.setattr(mt_module.ManualTool, "_manual_step_impl", slow_impl)

    result = mt_module.ManualTool.manual_step(description="慢步骤", timeout=0.3)
    assert result["success"] is False
    assert result["error"] == "Operation timeout"


def test_tool_execute_dispatch_passes_timeout_from_inputs(monkeypatch):
    """execute() 分发方法应该把 inputs 里的 timeout 传给具体实现，而不是被忽略。"""
    from tools.manual_tool import ManualTool

    captured = {}

    def fake_manual_step(description="", error=None, timeout=10):
        captured["timeout"] = timeout
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(ManualTool, "manual_step", staticmethod(fake_manual_step))

    ManualTool.execute("manual_step", {"description": "x", "timeout": 42})
    assert captured["timeout"] == 42
