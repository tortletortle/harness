# -*- coding: utf-8 -*-
"""技能包机制测试（生态层）：加载、工具注册、执行协议、超时、知识包、安全边界。"""
import json
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
sys.path.insert(0, str(PYTHON_DIR))

from skills_loader import SkillCapabilityAdapter, SkillManager  # noqa: E402
from capabilities import CapabilityRequest, CapabilityRegistry  # noqa: E402
from authorization import AuthorizationManager, GrantLimits  # noqa: E402
from tools import ToolRegistry  # noqa: E402

ECHO_SCRIPT = (
    "import json, sys\n"
    "req = json.loads(sys.stdin.read() or '{}')\n"
    "json.dump({'success': True, 'echo_inputs': req.get('inputs'), "
    "'instruction': req.get('instruction')}, sys.stdout, ensure_ascii=False)\n"
)
SLEEP_SCRIPT = (
    "import json, sys, time\n"
    "time.sleep(5)\n"
    "json.dump({'success': True}, sys.stdout)\n"
)


def _make_skill(root: Path, dirname: str, frontmatter: str, script: str = None) -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\n" + frontmatter + "\n---\n\n# 正文说明\n", encoding="utf-8")
    if script is not None:
        (d / "scripts").mkdir(exist_ok=True)
        (d / "scripts" / "run.py").write_text(script, encoding="utf-8")
    return d


@pytest.fixture()
def skill_root(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    _make_skill(
        root,
        "echo",
        "name: echo\ndescription: 回显输入的测试技能\nversion: 1.2.0\n"
        'entry: scripts/run.py\nruntime: python\ntimeout: 10\nside_effect: reversible\n',
        ECHO_SCRIPT,
    )
    _make_skill(
        root,
        "guide",
        "name: guide\ndescription: 纯知识包示例\n",
        None,
    )
    _make_skill(root, "broken", "name: broken\n", None)  # 缺 description
    return root


def test_load_parses_and_skips(skill_root):
    mgr = SkillManager(skill_root)
    mgr.load()
    assert {s.tool_id for s in mgr.skills} == {"skill_echo", "skill_guide"}
    assert mgr.skipped and mgr.skipped[0]["skill"] == "broken"
    entry = mgr.by_tool_id("skill_echo")
    assert entry.has_entry and entry.version == "1.2.0" and entry.side_effect == "reversible"
    assert not mgr.by_tool_id("skill_guide").has_entry


def test_entry_skill_executes_protocol(skill_root):
    mgr = SkillManager(skill_root)
    mgr.load()
    skill = mgr.by_tool_id("skill_echo")
    out = skill.execute("run it", {"msg": "你好"})
    assert out["success"] is True
    assert out["echo_inputs"] == {"msg": "你好"}
    assert out["instruction"] == "run it"


def test_entry_skill_timeout_reports_honestly(skill_root, tmp_path):
    d = _make_skill(
        skill_root,
        "slow",
        "name: slow\ndescription: 慢脚本\nentry: scripts/run.py\ntimeout: 0.5\n",
        SLEEP_SCRIPT,
    )
    mgr = SkillManager(skill_root)
    mgr.load()
    skill = mgr.by_tool_id("skill_slow")
    out = skill.execute("x", {})
    assert out["success"] is False
    assert "超时" in out["error"]
    assert d.exists()


def test_knowledge_skill_cannot_execute(skill_root):
    mgr = SkillManager(skill_root)
    mgr.load()
    out = mgr.by_tool_id("skill_guide").execute("x", {})
    assert out["success"] is False
    assert "没有可执行入口" in out["error"]


def test_entry_escape_rejected(skill_root):
    _make_skill(
        skill_root,
        "escape",
        "name: escape\ndescription: 越界 entry\nentry: ../outside.py\n",
        None,
    )
    mgr = SkillManager(skill_root)
    mgr.load()
    assert mgr.by_tool_id("skill_escape") is None
    assert any(s["skill"] == "escape" for s in mgr.skipped)


def test_register_into_tool_registry_and_execute(skill_root):
    mgr = SkillManager(skill_root)
    mgr.load()
    registry = ToolRegistry()
    registered = mgr.register_into(registry)
    assert registered == ["skill_echo"]
    metas = {m["tool_id"]: m for m in registry.list_tools()}
    assert metas["skill_echo"]["category"] == "skill"
    assert metas["skill_echo"]["description"].startswith("[技能包]")
    result = registry.execute_tool("skill_echo", "execute", {"k": "v"})
    assert result["success"] is True and result["echo_inputs"] == {"k": "v"}


def test_capability_adapter_spec_from_frontmatter(skill_root):
    mgr = SkillManager(skill_root)
    mgr.load()
    registry = ToolRegistry()
    mgr.register_into(registry)
    adapter = SkillCapabilityAdapter(registry, "skill_echo", mgr.by_tool_id("skill_echo"))
    spec = adapter.spec()
    assert spec.kind == "deterministic_tool"
    assert spec.side_effect == "reversible"
    assert spec.description.startswith("[技能包] 回显输入")


@pytest.mark.asyncio
async def test_skill_via_capability_registry_with_grant(skill_root, tmp_path):
    """对话助手同款路径：CapabilityRegistry + Grant 授权后调用技能。"""
    mgr = SkillManager(skill_root)
    mgr.load()
    tool_registry = ToolRegistry()
    mgr.register_into(tool_registry)
    cap_registry = CapabilityRegistry()
    cap_registry.register(SkillCapabilityAdapter(tool_registry, "skill_echo", mgr.by_tool_id("skill_echo")))

    manager = AuthorizationManager(workspace_root=tmp_path)
    grant = manager.issue_grant(
        "chat:test", ["skill_echo"], limits=GrantLimits(max_tool_calls=2, max_model_calls=0)
    )
    result = await cap_registry.run(
        "skill_echo",
        CapabilityRequest(
            work_item_id="chat:test", goal="x", inputs={"hello": "world"}, grant_id=grant.grant_id
        ),
        {"authorization_manager": manager, "grant_id": grant.grant_id},
    )
    assert result.status == "completed"
    assert result.output["echo_inputs"] == {"hello": "world"}


def test_disabled_config_yields_empty_manager(tmp_path):
    mgr = SkillManager.from_config({"skills": {"enabled": False}}, base_dir=tmp_path)
    mgr.load()
    assert mgr.skills == []
