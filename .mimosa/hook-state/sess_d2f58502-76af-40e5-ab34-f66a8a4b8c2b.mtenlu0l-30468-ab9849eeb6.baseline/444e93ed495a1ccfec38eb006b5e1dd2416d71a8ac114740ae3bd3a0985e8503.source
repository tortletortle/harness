# -*- coding: utf-8 -*-
"""
阶段1.4回归测试：审计日志表
- audit_logs 表创建成功
- save_audit_log / list_audit_logs 正常工作
- executor 在真实执行工具时（包括被安全拦截时）会写入审计日志
"""
import sqlite3
from uuid import uuid4

import pytest

from database import Database
from executor import DefaultExecutor
from models import Step
from tools import get_registry


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_audit.db"
    return Database(str(db_path))


@pytest.fixture
def executor(db):
    return DefaultExecutor(
        llm_client=None,
        model_config={},
        tool_registry=get_registry(),
        database=db,
    )


def test_audit_logs_table_created(db):
    conn = sqlite3.connect(db.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'")
        assert cursor.fetchone() is not None
    finally:
        conn.close()


def test_save_audit_log_normal(db):
    ok = db.save_audit_log(
        tool_id="command_runner",
        instruction="run_command",
        inputs={"command": "echo hi"},
        plan_id="plan-1",
        step_id="step-1",
        success=True,
        blocked=False,
    )
    assert ok is True


def test_save_audit_log_blocked(db):
    ok = db.save_audit_log(
        tool_id="command_runner",
        instruction="run_command",
        inputs={"command": "rm -rf /"},
        plan_id="plan-1",
        step_id="step-2",
        success=False,
        blocked=True,
        block_reason="Dangerous command blocked",
    )
    assert ok is True


def test_list_audit_logs_query(db):
    db.save_audit_log(
        tool_id="command_runner",
        instruction="run_command",
        inputs={"command": "echo hi"},
        plan_id="plan-1",
        step_id="step-1",
        success=True,
        blocked=False,
    )
    db.save_audit_log(
        tool_id="command_runner",
        instruction="run_command",
        inputs={"command": "rm -rf /"},
        plan_id="plan-1",
        step_id="step-2",
        success=False,
        blocked=True,
        block_reason="Dangerous command blocked",
    )

    logs = db.list_audit_logs(limit=10)
    assert len(logs) == 2
    assert logs[0]["instruction"] == "run_command"
    assert logs[0]["blocked"] == 1

    logs_by_plan = db.list_audit_logs(limit=10, plan_id="plan-1")
    assert len(logs_by_plan) == 2

    logs_by_other_plan = db.list_audit_logs(limit=10, plan_id="nonexistent")
    assert len(logs_by_other_plan) == 0


@pytest.mark.asyncio
async def test_executor_writes_audit_log_for_blocked_command(db, executor):
    step = Step(
        step_id=uuid4(),
        order=1,
        description="测试危险命令拦截审计",
        tool="command_runner:run_command",
        inputs={"command": "rm -rf /", "timeout": 5},
        check_criteria="手动检查",
    )

    result = await executor.execute_step(step)
    assert result.get("success") is False

    logs_after = db.list_audit_logs(limit=10)
    assert len(logs_after) == 1
    latest = logs_after[0]
    assert latest["tool_id"] == "command_runner"
    assert latest["blocked"] == 1
    assert latest["block_reason"] == "Dangerous command blocked"
    assert latest["step_id"] == str(step.step_id)


@pytest.mark.asyncio
async def test_executor_writes_audit_log_for_normal_command(db, executor):
    step = Step(
        step_id=uuid4(),
        order=2,
        description="测试正常命令审计",
        tool="command_runner:run_command",
        inputs={"command": "echo audit_test_ok", "timeout": 5},
        check_criteria="手动检查",
    )

    result = await executor.execute_step(step)
    assert result.get("success") is True

    logs_final = db.list_audit_logs(limit=10)
    assert len(logs_final) == 1
    latest = logs_final[0]
    assert latest["blocked"] == 0
    assert latest["success"] == 1


@pytest.mark.asyncio
async def test_executor_without_database_still_works():
    executor_no_db = DefaultExecutor(
        llm_client=None,
        model_config={},
        tool_registry=get_registry(),
    )
    step = Step(
        step_id=uuid4(),
        order=1,
        description="测试无DB时正常执行",
        tool="command_runner:run_command",
        inputs={"command": "echo audit_test_ok", "timeout": 5},
        check_criteria="手动检查",
    )
    result = await executor_no_db.execute_step(step)
    assert result.get("success") is True
