# -*- coding: utf-8 -*-
"""设计阶段缺口工具：别名映射、技能包合成、注册后可执行。"""
import asyncio
import json
from uuid import uuid4
from datetime import datetime, timezone

import pytest

from capabilities import CapabilityRegistry
from llm_client import LLMClient, LLMResponse, LLMProvider, Message
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ToolAssessment,
    ToolRequirement,
)
from skills_loader import SkillManager
from tool_synthesis import allowed_plan_tools, canonical_tool_id, fulfill_design_tools
from utils.json_fixer import validate_plan_tools
from tools import ToolRegistry


class _SynthProvider(LLMProvider):
    def __init__(self, payload: dict):
        self._payload = payload

    @property
    def name(self):
        return "stub"

    async def chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
        return LLMResponse(content=json.dumps(self._payload, ensure_ascii=False), model=model)

    async def health_check(self):
        return True


def _design(*tools: ToolRequirement) -> DesignDocument:
    return DesignDocument(
        design_id=uuid4(),
        user_request="做一个报表转换器",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        part1_design=DesignPart1(
            architecture=Architecture(components=["cli"], interactions=["stdin"]),
            difficulty=Difficulty(overall="easy", breakdown={"cli": "easy"}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=list(tools),
            overall_tool_completeness=0.0,
            risks=[],
        ),
    )


def test_canonical_maps_aliases():
    known = {"file_creator", "coding_agent", "skill_gen_csv"}
    assert canonical_tool_id("file-creator", known) == "file_creator"
    assert canonical_tool_id("code-generator", known) == "coding_agent"
    assert canonical_tool_id("csv", known) == "skill_gen_csv"


def test_allowed_plan_tools_includes_registry_inventory():
    """计划允许集 = 注册表 ∪ 核心 ∪ skill，不靠抹掉内置工具做安全。"""
    allowed = allowed_plan_tools(
        ["file_creator", "command_runner", "skill_gen_csv", "super_search"],
        ["skill_extra"],
    )
    assert "file_creator" in allowed
    assert "command_runner" in allowed
    assert "super_search" in allowed
    assert "skill_gen_csv" in allowed
    assert "skill_extra" in allowed
    assert "coding_agent" in allowed  # PLAN_CORE 兜底


def test_validate_plan_tools_rejects_unknown():
    ok, err = validate_plan_tools(
        {"segments": [{"steps": [{"tool": "command_runner", "inputs": {}}]}]},
        {"file_creator", "coding_agent"},
    )
    assert ok is False
    assert "command_runner" in (err or "")

    ok2, err2 = validate_plan_tools(
        {"segments": [{"steps": [{"tool": "file_creator", "inputs": {"path": "a"}}]}]},
        {"file_creator"},
    )
    assert ok2 is True
    assert err2 is None


@pytest.mark.asyncio
async def test_fulfill_maps_existing_without_llm():
    design = _design(ToolRequirement(tool_id="file-creator", availability="unavailable"))
    out = await fulfill_design_tools(design, known_ids=["file_creator"])
    assert out.part2_tool_assessment.required_tools[0].tool_id == "file_creator"
    assert out.part2_tool_assessment.required_tools[0].availability == "available"
    assert out.part2_tool_assessment.overall_tool_completeness == 1.0


@pytest.mark.asyncio
async def test_fulfill_does_not_fake_unregistered():
    """未注册且非 PLAN_CORE 的名字不得假绿。"""
    design = _design(
        ToolRequirement(tool_id="command_runner", availability="available"),
        ToolRequirement(tool_id="totally_missing_xyz", availability="available"),
    )
    out = await fulfill_design_tools(design, known_ids=[])
    ids = {t.tool_id: t.availability for t in out.part2_tool_assessment.required_tools}
    assert ids["command_runner"] == "unavailable"
    assert ids["totally_missing_xyz"] == "unavailable"
    assert out.part2_tool_assessment.overall_tool_completeness == 0.0


@pytest.mark.asyncio
async def test_fulfill_marks_registered_inventory_available():
    """注册表内的 search/git/run 等应标 available（别名映射到真实 id）。"""
    design = _design(
        ToolRequirement(tool_id="command_runner", availability="unavailable"),
        ToolRequirement(tool_id="search", availability="unavailable"),
        ToolRequirement(tool_id="file_creator", availability="unavailable"),
    )
    out = await fulfill_design_tools(
        design,
        known_ids=["command_runner", "super_search", "file_creator", "git_operator"],
    )
    by_id = {t.tool_id: t.availability for t in out.part2_tool_assessment.required_tools}
    assert by_id["command_runner"] == "available"
    assert by_id["super_search"] == "available"  # search → super_search
    assert by_id["file_creator"] == "available"


@pytest.mark.asyncio
async def test_fulfill_maps_plan_core_alias_without_registry():
    design = _design(ToolRequirement(tool_id="lint", availability="unavailable"))
    out = await fulfill_design_tools(design, known_ids=[])
    req = out.part2_tool_assessment.required_tools[0]
    assert req.tool_id == "lint_gate"
    assert req.availability == "available"


@pytest.mark.asyncio
async def test_fulfill_synthesizes_and_registers(tmp_path):
    script = (
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'success': True, 'ok': True, 'instruction': payload.get('instruction')}))\n"
    )
    client = LLMClient()
    client.register_provider(_SynthProvider({
        "name": "csv convert",
        "description": "把表格转成 csv",
        "side_effect": "reversible",
        "script": script,
    }))
    manager = SkillManager(tmp_path / "skills", {})
    manager.skills_dir.mkdir(parents=True)
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.tools = {}
    registry.aliases = {}
    caps = CapabilityRegistry()
    design = _design(ToolRequirement(
        tool_id="csv_convert", availability="unavailable", impact="导出 csv",
    ))
    out = await fulfill_design_tools(
        design,
        known_ids=[],
        llm_client=client,
        model_config={"primary": {"provider": "stub", "model": "stub-model", "max_tokens": 800}},
        skill_manager=manager,
        tool_registry=registry,
        capability_registry=caps,
    )
    tool_id = out.part2_tool_assessment.required_tools[0].tool_id
    assert tool_id == "skill_gen_csv_convert"
    assert out.part2_tool_assessment.required_tools[0].availability == "available"
    assert tool_id in registry.tools
    assert caps.get(tool_id) is not None
    result = registry.tools[tool_id].execute("run", {"path": "out.csv"})
    assert result.get("success") is True
    assert (tmp_path / "skills" / "gen_csv_convert" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_generate_plan_blocks_when_tools_missing(tmp_path):
    from designer import DefaultDesigner, GenerationError
    from cache import DesignCache, PlanCache

    designer = DefaultDesigner(
        LLMClient(),
        {"primary": {"provider": "stub", "model": "m", "temperature": 0.1}},
        dispatcher=None,
    )
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "d.json"))
    designer.plan_cache = PlanCache(cache_file=str(tmp_path / "p.json"))
    # 无 skill_manager → 缺口无法合成，保持 unavailable
    design = _design(ToolRequirement(tool_id="totally_missing_tool", availability="unavailable"))
    with pytest.raises(GenerationError, match="工具未齐"):
        await designer.generate_plan(design)


@pytest.mark.asyncio
async def test_generate_plan_discards_cache_with_forbidden_tool(tmp_path):
    from designer import DefaultDesigner
    from cache import DesignCache, PlanCache
    from uuid import uuid4

    calls = {"n": 0}
    plan_json = {
        "segments": [{
            "description": "ok",
            "steps": [{
                "description": "建文件",
                "tool": "file_creator",
                "inputs": {"path": "a.txt", "content": "x"},
                "acceptance": [{"type": "file_exists", "path": "a.txt", "kind": "file"}],
            }],
        }],
        "implementation_list": ["a.txt"],
    }

    class _PlanLLM:
        async def chat(self, **kwargs):
            calls["n"] += 1
            return type("R", (), {"content": json.dumps(plan_json, ensure_ascii=False)})()

    designer = DefaultDesigner(
        _PlanLLM(),
        {"primary": {"provider": "stub", "model": "m", "temperature": 0.1}},
        dispatcher=None,
    )
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "d.json"))
    designer.plan_cache = PlanCache(cache_file=str(tmp_path / "p.json"))
    design = _design()
    # 旧缓存含未注册 tool（dispatcher=None 时允许集只有 PLAN_CORE）
    designer.plan_cache.set(str(design.design_id), {
        "plan_id": str(uuid4()),
        "design_id": str(design.design_id),
        "user_request": design.user_request,
        "segments": [{
            "segment_id": str(uuid4()),
            "description": "bad",
            "steps": [{
                "step_id": str(uuid4()),
                "order": 1,
                "description": "ghost",
                "tool": "not_a_real_tool_xyz",
                "inputs": {},
                "check_criteria": "ok",
                "depends_on": [],
                "acceptance": [],
            }],
        }],
        "status": "Pending",
        "implementation_list": [],
    })
    plan = await designer.generate_plan(design, skip_fulfill=True)
    assert calls["n"] == 1
    assert plan.segments[0].steps[0].tool == "file_creator"


@pytest.mark.asyncio
async def test_skip_fulfill_does_not_call_synthesis(tmp_path, monkeypatch):
    from designer import DefaultDesigner
    from cache import DesignCache, PlanCache
    import tool_synthesis as ts

    called = {"fulfill": 0}

    async def boom(*args, **kwargs):
        called["fulfill"] += 1
        raise AssertionError("should not fulfill")

    monkeypatch.setattr(ts, "fulfill_design_tools", boom)
    plan_json = {
        "segments": [{
            "description": "ok",
            "steps": [{
                "description": "建文件",
                "tool": "file_creator",
                "inputs": {"path": "a.txt", "content": "x"},
                "acceptance": [{"type": "file_exists", "path": "a.txt", "kind": "file"}],
            }],
        }],
        "implementation_list": ["a.txt"],
    }

    class _PlanLLM:
        async def chat(self, **kwargs):
            return type("R", (), {"content": json.dumps(plan_json, ensure_ascii=False)})()

    designer = DefaultDesigner(
        _PlanLLM(),
        {"primary": {"provider": "stub", "model": "m", "temperature": 0.1}},
        dispatcher=None,
    )
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "d.json"))
    designer.plan_cache = PlanCache(cache_file=str(tmp_path / "p.json"))
    design = _design(ToolRequirement(tool_id="file_creator", availability="available"))
    plan = await designer.generate_plan(design, skip_fulfill=True)
    assert called["fulfill"] == 0
    assert plan.segments[0].steps[0].tool == "file_creator"


@pytest.mark.asyncio
async def test_fulfill_rejects_banned_script_and_marks_unavailable(tmp_path):
    client = LLMClient()
    client.register_provider(_SynthProvider({
        "name": "bad",
        "description": "x",
        "side_effect": "reversible",
        "script": "import subprocess\nprint('{\"success\": true}')\n",
    }))
    manager = SkillManager(tmp_path / "skills", {})
    manager.skills_dir.mkdir(parents=True)
    design = _design(ToolRequirement(tool_id="shell_bomb", availability="unavailable"))
    out = await fulfill_design_tools(
        design,
        known_ids=[],
        llm_client=client,
        model_config={"primary": {"provider": "stub", "model": "m"}},
        skill_manager=manager,
        tool_registry=ToolRegistry.__new__(ToolRegistry),
        capability_registry=CapabilityRegistry(),
    )
    # ToolRegistry.__new__ has no tools attr → register may fail after write;
    # banned script should fail before write.
    assert out.part2_tool_assessment.required_tools[0].availability == "unavailable"
    assert not (tmp_path / "skills" / "gen_shell_bomb").exists()


@pytest.mark.asyncio
async def test_generate_plan_feeds_back_illegal_tool(tmp_path):
    """非法 tool 拒绝后，下一轮 messages 必须带上短反馈。"""
    from designer import DefaultDesigner
    from cache import DesignCache, PlanCache

    bad = {
        "segments": [{
            "description": "bad",
            "steps": [{
                "description": "ghost",
                "tool": "not_a_real_tool_xyz",
                "inputs": {},
                "acceptance": [],
            }],
        }],
        "implementation_list": [],
    }
    good = {
        "segments": [{
            "description": "ok",
            "steps": [{
                "description": "建文件",
                "tool": "file_creator",
                "inputs": {"path": "a.txt", "content": "x"},
                "acceptance": [{"type": "file_exists", "path": "a.txt", "kind": "file"}],
            }],
        }],
        "implementation_list": ["a.txt"],
    }
    seen: list = []

    class _PlanLLM:
        async def chat(self, **kwargs):
            msgs = kwargs.get("messages") or []
            seen.append(list(msgs))
            payload = bad if len(seen) == 1 else good
            return type("R", (), {"content": json.dumps(payload, ensure_ascii=False)})()

    designer = DefaultDesigner(
        _PlanLLM(),
        {"primary": {"provider": "stub", "model": "m", "temperature": 0.1}},
        dispatcher=None,
    )
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "d.json"))
    designer.plan_cache = PlanCache(cache_file=str(tmp_path / "p.json"))
    design = _design(ToolRequirement(tool_id="file_creator", availability="available"))
    plan = await designer.generate_plan(design, skip_fulfill=True)
    assert len(seen) >= 2
    second = seen[1]
    blob = " ".join(getattr(m, "content", "") or "" for m in second)
    assert "被拒绝" in blob
    assert "not_a_real_tool_xyz" in blob
    assert plan.segments[0].steps[0].tool == "file_creator"


@pytest.mark.asyncio
async def test_generate_plan_feeds_back_invalid_json(tmp_path):
    from designer import DefaultDesigner
    from cache import DesignCache, PlanCache

    good = {
        "segments": [{
            "description": "ok",
            "steps": [{
                "description": "建文件",
                "tool": "file_creator",
                "inputs": {"path": "a.txt", "content": "x"},
                "acceptance": [{"type": "file_exists", "path": "a.txt", "kind": "file"}],
            }],
        }],
        "implementation_list": ["a.txt"],
    }
    seen: list = []

    class _PlanLLM:
        async def chat(self, **kwargs):
            msgs = kwargs.get("messages") or []
            seen.append(list(msgs))
            if len(seen) == 1:
                return type("R", (), {"content": "这不是合法计划 JSON"})()
            return type("R", (), {"content": json.dumps(good, ensure_ascii=False)})()

    designer = DefaultDesigner(
        _PlanLLM(),
        {"primary": {"provider": "stub", "model": "m", "temperature": 0.1}},
        dispatcher=None,
    )
    designer.design_cache = DesignCache(cache_file=str(tmp_path / "d.json"))
    designer.plan_cache = PlanCache(cache_file=str(tmp_path / "p.json"))
    design = _design(ToolRequirement(tool_id="file_creator", availability="available"))
    plan = await designer.generate_plan(design, skip_fulfill=True)
    assert len(seen) >= 2
    blob = " ".join(getattr(m, "content", "") or "" for m in seen[1])
    assert "被拒绝" in blob
    assert "JSON" in blob
    assert plan.segments[0].steps[0].tool == "file_creator"


def test_skill_manager_from_config_anchors_repo_root(tmp_path, monkeypatch):
    from paths import REPO_ROOT
    from skills_loader import SkillManager

    monkeypatch.chdir(tmp_path)
    mgr = SkillManager.from_config({"skills": {"dir": "skills", "enabled": True}})
    assert mgr.skills_dir == REPO_ROOT / "skills"

    nested = tmp_path / "custom"
    nested.mkdir()
    mgr2 = SkillManager.from_config(
        {"skills": {"dir": "skills", "enabled": True}},
        base_dir=tmp_path,
    )
    assert mgr2.skills_dir == tmp_path / "skills"


@pytest.mark.asyncio
async def test_fulfill_persists_synthesized_tool_to_catalog(tmp_path):
    from database import Database

    script = (
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'success': True, 'ok': True}))\n"
    )
    client = LLMClient()
    client.register_provider(_SynthProvider({
        "name": "db skill",
        "description": "persist me",
        "side_effect": "reversible",
        "script": script,
    }))
    manager = SkillManager(tmp_path / "skills", {})
    manager.skills_dir.mkdir(parents=True)
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.tools = {}
    registry.aliases = {}
    db = Database(str(tmp_path / "catalog.db"))
    design = _design(ToolRequirement(
        tool_id="persist_me", availability="unavailable", impact="落库",
    ))
    out = await fulfill_design_tools(
        design,
        known_ids=[],
        llm_client=client,
        model_config={"primary": {"provider": "stub", "model": "stub-model", "max_tokens": 800}},
        skill_manager=manager,
        tool_registry=registry,
        capability_registry=CapabilityRegistry(),
        database=db,
    )
    tool_id = out.part2_tool_assessment.required_tools[0].tool_id
    row = db.get_tool_catalog(tool_id)
    assert row is not None
    assert row["status"] == "ready"
    assert row["kind"] == "skill"
    assert row["source"] == "synthesized"
    assert str(design.design_id) == row["design_id"]


def test_sync_tool_catalog_marks_missing(tmp_path):
    from database import Database
    from types import SimpleNamespace

    db = Database(str(tmp_path / "sync.db"))
    db.upsert_tool_catalog(
        "skill_gen_gone",
        kind="skill",
        name="gone",
        skill_dir="skills/gen_gone",
        status="ready",
        source="synthesized",
    )
    present = SimpleNamespace(
        tool_id="skill_gen_keep",
        name="keep",
        description="still here",
        has_entry=True,
        dir=tmp_path / "skills" / "gen_keep",
    )
    stats = db.sync_tool_catalog_skills([present], repo_root=tmp_path)
    assert stats["ready"] == 1
    assert stats["missing"] == 1
    assert db.get_tool_catalog("skill_gen_keep")["status"] == "ready"
    assert db.get_tool_catalog("skill_gen_gone")["status"] == "missing"


@pytest.mark.asyncio
async def test_fulfill_rejects_catalog_missing_skill(tmp_path):
    from database import Database

    db = Database(str(tmp_path / "miss.db"))
    db.upsert_tool_catalog(
        "skill_gen_gone",
        kind="skill",
        name="gone",
        status="missing",
        source="synthesized",
    )
    design = _design(
        ToolRequirement(tool_id="skill_gen_gone", availability="available"),
        ToolRequirement(tool_id="file_creator", availability="unavailable"),
    )
    out = await fulfill_design_tools(
        design,
        known_ids=["skill_gen_gone", "file_creator"],
        database=db,
    )
    by_id = {t.tool_id: t.availability for t in out.part2_tool_assessment.required_tools}
    assert by_id["skill_gen_gone"] == "unavailable"
    assert by_id["file_creator"] == "available"
