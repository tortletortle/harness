"""
P0 修复验证

覆盖：
1. auth 模块：token 生成/校验/常数时间比较
2. update_step / update_plan：状态值校验与返回值
3. check_result 三态
4. execute_step 在校验失败时判定步骤失败
5. format_plan_for_frontend 包含 outputs
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


# ===== 1. auth =====
os.environ["EAS_API_TOKEN"] = "test-token-abc123"
from api import auth

auth.reset_token_cache()
check("token 从环境变量读取", auth.load_token() == "test-token-abc123")
check("正确 token 通过", auth.verify_token("test-token-abc123"))
check("错误 token 拒绝", not auth.verify_token("wrong"))
check("空 token 拒绝", not auth.verify_token(""))
check("None 拒绝", not auth.verify_token(None))
check("Bearer 解析", auth._extract_bearer("Bearer xyz") == "xyz")
check("bearer 大小写不敏感", auth._extract_bearer("bearer xyz") == "xyz")
check("非 Bearer 方案返回 None", auth._extract_bearer("Basic xyz") is None)
check("畸形头返回 None", auth._extract_bearer("Bearer") is None)


async def test_require_token():
    from fastapi import HTTPException
    # 合法
    try:
        await auth.require_token(authorization="Bearer test-token-abc123", x_api_token=None)
        ok_valid = True
    except HTTPException:
        ok_valid = False
    check("require_token 接受合法 Bearer", ok_valid)

    # X-API-Token 头
    try:
        await auth.require_token(authorization=None, x_api_token="test-token-abc123")
        ok_header = True
    except HTTPException:
        ok_header = False
    check("require_token 接受 X-API-Token", ok_header)

    # 无凭据 -> 401
    try:
        await auth.require_token(authorization=None, x_api_token=None)
        ok_reject = False
        code = None
    except HTTPException as e:
        ok_reject = True
        code = e.status_code
    check("require_token 无凭据抛 401", ok_reject and code == 401, f"code={code}")

    # 错误 token -> 401
    try:
        await auth.require_token(authorization="Bearer nope", x_api_token=None)
        ok_bad = False
    except HTTPException as e:
        ok_bad = e.status_code == 401
    check("require_token 错误 token 抛 401", ok_bad)


# ===== 2. database =====
from database import Database
from models import StepStatus, PlanStatus

tmpdir = tempfile.mkdtemp()
db = Database(str(Path(tmpdir) / "t.db"))

check("update_step 拒绝非法状态 COMPLETED", db.update_step("x", "COMPLETED") is False)
check("update_step 拒绝非法状态 FAILED", db.update_step("x", "FAILED") is False)
check("update_step 拒绝小写 success", db.update_step("x", "success") is False)
check("update_step 不存在的 step_id 返回 False",
      db.update_step(str(uuid.uuid4()), "Success") is False)
check("update_plan 拒绝非法状态", db.update_plan("x", "DONE") is False)
check("update_plan 拒绝 Success（非 PlanStatus）", db.update_plan("x", "Success") is False)
check("update_plan 不存在的 plan_id 返回 False",
      db.update_plan(str(uuid.uuid4()), "Completed") is False)

# 外键是否真的开启
import sqlite3
conn = db._get_connection()
fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
conn.close()
check("PRAGMA foreign_keys 已开启", fk == 1, f"值={fk}")


# ===== 3 & 4. executor 三态与失败判定 =====
from executor import DefaultExecutor
from models import Step


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
    return Step(
        step_id=uuid.uuid4(),
        order=1,
        description="测试步骤",
        tool="file_creator",
        inputs={"path": "a.txt", "content": "x"},
        check_criteria=criteria,
        status=StepStatus.PENDING,
    )


async def test_executor():
    tool_ok = {"success": True, "message": "done"}

    # 校验通过
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    check("check_result YES -> passed", st == DefaultExecutor.CHECK_PASSED, st)

    # 校验失败
    ex = DefaultExecutor(FakeLLM("NO"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    check("check_result NO -> failed", st == DefaultExecutor.CHECK_FAILED, st)

    # LLM 异常 -> unverified（不再默认通过）
    ex = DefaultExecutor(FakeLLM(raise_exc=True), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    check("check_result LLM 异常 -> unverified", st == DefaultExecutor.CHECK_UNVERIFIED, st)

    # 配置缺 primary -> unverified 而非崩溃/通过
    ex = DefaultExecutor(FakeLLM("YES"), {}, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    check("check_result 配置缺失 -> unverified", st == DefaultExecutor.CHECK_UNVERIFIED, st)

    # 无标准 -> unverified
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(criteria=""), tool_ok)
    check("check_result 无标准 -> unverified", st == DefaultExecutor.CHECK_UNVERIFIED, st)

    # 回复无法解析 -> unverified
    ex = DefaultExecutor(FakeLLM("大概吧"), GOOD_CFG, FakeRegistry(tool_ok))
    st = await ex.check_result(make_step(), tool_ok)
    check("check_result 无法解析 -> unverified", st == DefaultExecutor.CHECK_UNVERIFIED, st)

    # 关键：工具成功但校验失败 -> 步骤必须失败
    ex = DefaultExecutor(FakeLLM("NO"), GOOD_CFG, FakeRegistry(tool_ok))
    res = await ex.execute_step(make_step())
    check("工具成功+校验失败 -> success=False", res["success"] is False)
    check("失败时 check_status=failed", res["check_status"] == "failed", res["check_status"])
    check("失败时 error 非空", bool(res["error"]), str(res["error"])[:60])

    # 工具成功且校验通过 -> 成功
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG, FakeRegistry(tool_ok))
    res = await ex.execute_step(make_step())
    check("工具成功+校验通过 -> success=True", res["success"] is True)
    check("成功时 check_passed=True", res["check_passed"] is True)

    # unverified 仍算成功，但 check_passed=False
    ex = DefaultExecutor(FakeLLM(raise_exc=True), GOOD_CFG, FakeRegistry(tool_ok))
    res = await ex.execute_step(make_step())
    check("unverified -> success=True", res["success"] is True)
    check("unverified -> check_passed=False", res["check_passed"] is False)
    check("unverified -> check_status=unverified",
          res["check_status"] == "unverified", res["check_status"])

    # 工具本身失败
    ex = DefaultExecutor(FakeLLM("YES"), GOOD_CFG,
                         FakeRegistry({"success": False, "message": "boom", "error": "boom"}))
    res = await ex.execute_step(make_step())
    check("工具失败 -> success=False", res["success"] is False)


# ===== 5. format_plan_for_frontend =====
def test_format():
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
    check("前端载荷含 outputs 字段", "outputs" in step)
    check("失败原因可见", step.get("outputs", {}).get("error") == "磁盘写满了",
          str(step.get("outputs")))
    check("含 executed_at", step.get("executed_at") is not None)
    check("segments 与 steps 同步", len(out["segments"][0]["steps"]) == 1)


async def main():
    await test_require_token()
    await test_executor()
    test_format()

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
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
