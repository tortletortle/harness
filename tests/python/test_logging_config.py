"""
阶段4.1验证：logging_config.py 的基本行为

覆盖点：
- get_logger() 返回可用的 logger，能正常记录不同级别的消息
- 日志文件按配置的目录/文件名创建，且写入内容可读
- 多次调用 configure_logging()/get_logger() 是幂等的（不会重复添加 sink）
- 未绑定 trace_id 时格式化为 "-"，不会报错（为阶段4.2 trace_id贯穿做的兼容性检查）
"""

import importlib
import sys

import pytest


@pytest.fixture
def isolated_logging_config(tmp_path, monkeypatch):
    """
    每个测试都在独立的临时目录里重新加载 logging_config 模块，
    避免多个测试之间共享全局 loguru sink 状态、避免污染仓库根目录的 logs/。
    """
    monkeypatch.chdir(tmp_path)

    # logging_config 和 loguru 的 logger 都是模块级全局单例，必须彻底移除后重新 import
    for mod_name in ("logging_config",):
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    from loguru import logger as loguru_logger
    loguru_logger.remove()

    module = importlib.import_module("logging_config")
    yield module, tmp_path

    # 清理：移除本次测试加的 sink，避免影响后续测试
    loguru_logger.remove()
    if "logging_config" in sys.modules:
        del sys.modules["logging_config"]


def test_get_logger_returns_usable_logger(isolated_logging_config):
    module, tmp_path = isolated_logging_config
    logger = module.get_logger(__name__)

    assert logger is not None
    # 不应抛异常
    logger.info("测试消息")
    logger.warning("警告: {}", "detail")
    logger.error("错误: {}", "err")
    logger.debug("调试消息")


def test_log_file_created_and_writable(isolated_logging_config):
    module, tmp_path = isolated_logging_config
    logger = module.get_logger(__name__)

    logger.info("hello from test")
    # loguru 文件 sink 配置了 enqueue=True（异步写入），需要 complete() 确保落盘
    logger.complete()

    log_path = tmp_path / "logs" / "eas.log"
    assert log_path.exists(), "logs/eas.log 应该被自动创建"

    content = log_path.read_text(encoding="utf-8")
    assert "hello from test" in content
    assert "INFO" in content


def test_configure_logging_is_idempotent(isolated_logging_config):
    module, tmp_path = isolated_logging_config

    module.configure_logging()
    module.configure_logging()
    module.configure_logging()

    logger = module.get_logger(__name__)
    logger.info("只应该出现一次")
    logger.complete()

    log_path = tmp_path / "logs" / "eas.log"
    content = log_path.read_text(encoding="utf-8")
    # 幂等：多次调用 configure_logging 不应导致同一条消息被多个重复 sink 写入多次
    assert content.count("只应该出现一次") == 1


def test_trace_id_defaults_to_placeholder_when_unbound(isolated_logging_config):
    module, tmp_path = isolated_logging_config
    logger = module.get_logger(__name__)

    logger.info("未绑定trace_id的消息")
    logger.complete()

    log_path = tmp_path / "logs" / "eas.log"
    content = log_path.read_text(encoding="utf-8")
    assert "trace=-" in content


def test_trace_id_bind_appears_in_log(isolated_logging_config):
    """为阶段4.2 预留：bind(trace_id=...) 应该能正确出现在日志里。"""
    module, tmp_path = isolated_logging_config
    logger = module.get_logger(__name__)

    bound_logger = logger.bind(trace_id="test-trace-123")
    bound_logger.info("带trace_id的消息")
    logger.complete()

    log_path = tmp_path / "logs" / "eas.log"
    content = log_path.read_text(encoding="utf-8")
    assert "trace=test-trace-123" in content
