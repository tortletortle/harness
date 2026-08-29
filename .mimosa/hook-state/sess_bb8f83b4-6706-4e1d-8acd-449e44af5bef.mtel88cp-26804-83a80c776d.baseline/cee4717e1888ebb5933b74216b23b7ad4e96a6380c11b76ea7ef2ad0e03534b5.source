"""
P0 修复验证

覆盖：
1. auth 模块：token 生成/校验/常数时间比较
2. update_step / update_plan：状态值校验与返回值
3. check_result 三态
4. execute_step 在校验失败时判定步骤失败
5. format_plan_for_frontend 包含 outputs
"""

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import auth

TEST_TOKEN = "test-token-abc123"


@pytest.fixture
def auth_env():
    """隔离 auth 的全局 token 缓存，避免污染其他测试文件。"""
    prev_env = os.environ.get(auth.ENV_VAR)
    os.environ[auth.ENV_VAR] = TEST_TOKEN
    auth.reset_token_cache()
    yield
    if prev_env is None:
        os.environ.pop(auth.ENV_VAR, None)
    else:
        os.environ[auth.ENV_VAR] = prev_env
    auth.reset_token_cache()


# ===== 1. auth =====

def test_token_from_env(auth_env):
    assert auth.load_token() == TEST_TOKEN


def test_verify_token_correct(auth_env):
    assert auth.verify_token(TEST_TOKEN)


def test_verify_token_wrong(auth_env):
    assert not auth.verify_token("wrong")


def test_verify_token_empty(auth_env):
    assert not auth.verify_token("")


def test_verify_token_none(auth_env):
    assert not auth.verify_token(None)


def test_extract_bearer(auth_env):
    assert auth._extract_bearer("Bearer xyz") == "xyz"


def test_extract_bearer_case_insensitive(auth_env):
    assert auth._extract_bearer("bearer xyz") == "xyz"


def test_extract_bearer_non_bearer_scheme(auth_env):
    assert auth._extract_bearer("Basic xyz") is None


def test_extract_bearer_malformed_header(auth_env):
    assert auth._extract_bearer("Bearer") is None


@pytest.mark.asyncio
async def test_require_token_accepts_valid_bearer(auth_env):
    await auth.require_token(authorization=f"Bearer {TEST_TOKEN}", x_api_token=None)


@pytest.mark.asyncio
async def test_require_token_accepts_x_api_token_header(auth_env):
    await auth.require_token(authorization=None, x_api_token=TEST_TOKEN)


@pytest.mark.asyncio
async def test_require_token_no_credentials_raises_401(auth_env):
    with pytest.raises(HTTPException) as exc_info:
        await auth.require_token(authorization=None, x_api_token=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_token_wrong_token_raises_401(auth_env):
    with pytest.raises(HTTPException) as exc_info:
        await auth.require_token(authorization="Bearer nope", x_api_token=None)
    assert exc_info.value.status_code == 401


# ===== 2. database =====

@pytest.fixture
def db(tmp_path):
    from database import Database
    return Database(str(tmp_path / "t.db"))


def test_update_step_rejects_illegal_status_completed(db):
    assert db.update_step("x", "COMPLETED") is False


def test_update_step_rejects_illegal_status_failed(db):
    assert db.update_step("x", "FAILED") is False


def test_update_step_rejects_lowercase_success(db):
    assert db.update_step("x", "success") is False


def test_update_step_nonexistent_step_id_returns_false(db):
    assert db.update_step(str(uuid.uuid4()), "Success") is False


def test_update_plan_rejects_illegal_status(db):
    assert db.update_plan("x", "DONE") is False


def test_update_plan_rejects_success_not_plan_status(db):
    assert db.update_plan("x", "Success") is False


def test_update_plan_nonexistent_plan_id_returns_false(db):
    assert db.update_plan(str(uuid.uuid4()), "Completed") is False


def test_pragma_foreign_keys_enabled(db):
    conn = db._get_connection()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()
    assert fk == 1


# ===== 3 & 4. executor 三态与失败判定 =====

class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    def __init__(self, reply=None, raise_exc=False):
        self.reply = reply
        self.raise_exc = raise_exc

    async def chat(self, **kwargs):
        if self.raise_exc:
            raise RuntimeError("LLM 挂了")
        return FakeResponse(self.reply)


class FakeRegistry:
    def __init__(self, result):
        self.result = result

    def execute_tool(self, tool_id, instruction, inputs):
        return self.result


GOOD_CFG = {"primary": {"provider": "p", "model": "m"}}


def make_step(criteria="文件应存在"):
    from models import Step, StepStatus
    return Step(
        step_id=uuid.uuid4(),
        order=1,
        description="测试步骤",
        tool="file_creator",
        inputs={"path": "a.txt", "content": "x"},
        check_criteria=criteria,
        status=StepStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_check_result_yes_passes():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    assert st == DefaultExecutor.CHECK_PASSED


@pytest.mark.asyncio
async def test_check_result_no_fails():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("NO"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    assert st == DefaultExecutor.CHECK_FAILED


@pytest.mark.asyncio
async def test_check_result_llm_exception_unverified():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM(raise_exc=True), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    assert st == DefaultExecutor.CHECK_UNVERIFIED


@pytest.mark.asyncio
async def test_check_result_missing_config_unverified():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("YES"), {}, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    assert st == DefaultExecutor.CHECK_UNVERIFIED


@pytest.mark.asyncio
async def test_check_result_no_criteria_unverified():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(criteria=""), tool_ok)
    assert st == DefaultExecutor.CHECK_UNVERIFIED


@pytest.mark.asyncio
async def test_check_result_unparseable_reply_unverified():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("大概吧"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    assert st == DefaultExecutor.CHECK_UNVERIFIED


@pytest.mark.asyncio
async def test_execute_step_tool_success_check_fails():
    """关键：工具成功但校验失败 -> 步骤必须失败"""
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("NO"), GOOD_CFG, FakeRegistry(tool_ok))
    res = await ex.execute_step(make_step())
    assert res["success"] is False
    assert res["check_status"] == "failed"
    assert bool(res["error"])


@pytest.mark.asyncio
async def test_execute_step_tool_success_check_passes():
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG, FakeRegistry(tool_ok))
    res = await ex.execute_step(make_step())
    assert res["success"] is True
    assert res["check_passed"] is True


@pytest.mark.asyncio
async def test_execute_step_unverified_still_succeeds():
    """unverified 仍算成功，但 check_passed=False"""
    from executor import DefaultExecutor
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM(raise_exc=True), GOOD_CFG, FakeRegistry(tool_ok))
    res = await ex.execute_step(make_step())
    assert res["success"] is True
    assert res["check_passed"] is False
    assert res["check_status"] == "unverified"


@pytest.mark.asyncio
async def test_execute_step_tool_failure():
    from executor import DefaultExecutor
    ex = DefaultExecutor(
        FakeLLM("YES"), GOOD_CFG,
        FakeRegistry({"success": False, "message": "boom", "error": "boom"}),
    )
    res = await ex.execute_step(make_step())
    assert res["success"] is False


# ===== 5. format_plan_for_frontend =====

def test_format_plan_for_frontend_includes_outputs():
    from api.server import format_plan_for_frontend
    sid = str(uuid.uuid4())
    plan = {
        "plan_id": str(uuid.uuid4()),
        "design_id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "segments": [{
            "segment_id": str(uuid.uuid4()),
            "description": "seg",
            "steps": [{
                "step_id": sid,
                "order": 1,
                "description": "d",
                "tool": "file_creator",
                "inputs": {},
                "check_criteria": "c",
                "status": "Failed",
                "outputs": {"error": "磁盘写满了"},
                "executed_at": datetime.now().isoformat(),
            }],
        }],
    }
    out = format_plan_for_frontend(plan)
    step = out["steps"][0]
    assert "outputs" in step
    assert step.get("outputs", {}).get("error") == "磁盘写满了"
    assert step.get("executed_at") is not None
    assert len(out["segments"][0]["steps"]) == 1
