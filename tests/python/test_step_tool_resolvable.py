# -*- coding: utf-8 -*-
"""执行期工具注册闸门：未注册 tool 不得走到审批/Grant。"""
from types import SimpleNamespace

from api.runtime.step_execution import step_tool_id, tool_is_resolvable


def test_step_tool_id_strips_instruction_suffix():
    assert step_tool_id("file_creator:write") == "file_creator"
    assert step_tool_id("coding_agent") == "coding_agent"
    assert step_tool_id(None) == ""


def test_tool_is_resolvable_via_registry_or_capability():
    registry = SimpleNamespace(tools={"file_creator": object()}, aliases={"fc": "file_creator"})
    caps = SimpleNamespace(get=lambda tid: object() if tid == "coding_agent" else None)

    assert tool_is_resolvable("file_creator", tool_registry=registry, capability_registry=caps)
    assert tool_is_resolvable("fc", tool_registry=registry, capability_registry=caps)
    assert tool_is_resolvable("coding_agent", tool_registry=registry, capability_registry=caps)
    assert not tool_is_resolvable("ghost_tool", tool_registry=registry, capability_registry=caps)
    assert not tool_is_resolvable("", tool_registry=registry, capability_registry=caps)


def test_sync_tool_catalog_registry(tmp_path):
    from database import Database

    db = Database(str(tmp_path / "reg.db"))
    n = db.sync_tool_catalog_registry([
        {"tool_id": "super_search", "name": "Super Search", "description": "search"},
        {"tool_id": "skill_gen_x", "name": "x", "description": "skill"},
        {"tool_id": "", "name": "skip"},
    ])
    assert n == 2
    assert db.get_tool_catalog("super_search")["kind"] == "builtin"
    assert db.get_tool_catalog("super_search")["status"] == "ready"
    assert db.get_tool_catalog("skill_gen_x")["kind"] == "skill"
