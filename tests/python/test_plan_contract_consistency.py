# -*- coding: utf-8 -*-
"""
验收-指令一致性守卫测试：
1) json_fixer.validate_agent_steps 拒绝"验收与指令零交集"的 coding_agent 步骤
   （真实首跑 step20/22 的失败模式：指令说"组件列表"，验收却检查 component_list）
2) 有交集/宽松验收/大小写不敏感时放行
3) file_creator 步骤不受该检查影响
4) Designer 计划提示词包含一致性规则且标记语言统一
"""
from utils.json_fixer import validate_agent_steps


def _coding_step(instruction, texts, description=""):
    return {
        "tool": "coding_agent",
        "description": description,
        "inputs": {
            "path": "src/app.py",
            "instruction": instruction,
            "allowed_tools": ["text_editor"],
        },
        "acceptance": [
            {"type": "content_contains", "path": "src/app.py", "text": t} for t in texts
        ],
    }


def test_zero_intersection_rejected():
    """真实首跑失败模式复现：指令中文描述，验收英文新造词 → 必须打回。"""
    plan = {"segments": [{"steps": [
        _coding_step(
            "实现generate方法，基于输入的特征向量生成包含组件列表、交互流程、接口定义的架构设计方案，返回设计方案字典",
            ["component_list", "interaction_flow"],
        )
    ]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is False
    assert "零交集" in err
    assert "component_list" in err


def test_intersection_passes():
    plan = {"segments": [{"steps": [
        _coding_step("把 return 1 改成 return 'hello, world'", ["hello, world"])
    ]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_def_name_style_passes():
    plan = {"segments": [{"steps": [
        _coding_step("实现 load_knowledge 方法，从指定路径加载知识库数据", ["def load_knowledge"])
    ]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_case_insensitive_intersection():
    plan = {"segments": [{"steps": [
        _coding_step("实现 Load_Knowledge 查询逻辑", ["load_knowledge"])
    ]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_description_counts_as_haystack():
    plan = {"segments": [{"steps": [
        _coding_step("实现查询逻辑", ["matched_entries"], description="返回 matched_entries 列表")
    ]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_partial_intersection_passes():
    """多条规则里只要有一条与指令有交集即放行（零交集才拒绝）。"""
    plan = {"segments": [{"steps": [
        _coding_step("实现 load_knowledge 数据加载，匹配后返回 matched_entries 列表", ["load_knowledge", "matched_entries"])
    ]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_file_creator_scope_unchanged():
    """零交集检查只作用于 coding_agent；file_creator 步骤不受影响。"""
    plan = {"segments": [{"steps": [{
        "tool": "file_creator",
        "description": "创建主程序",
        "inputs": {"path": "./project/main.py", "content": "print('hello')"},
        "acceptance": [
            {"type": "file_exists", "path": "./project/main.py", "kind": "file"},
            {"type": "content_contains", "path": "./project/main.py", "text": "完全无关的检查串"},
        ],
    }]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_empty_texts_no_check():
    """acceptance 里只有 file_exists（无 content_contains）→ 不触发交集检查。"""
    plan = {"segments": [{"steps": [{
        "tool": "coding_agent",
        "description": "x",
        "inputs": {"path": "src/a.py", "instruction": "改代码", "allowed_tools": ["text_editor"]},
        "acceptance": [{"type": "file_exists", "path": "src/a.py", "kind": "file"}],
    }]}]}
    ok, err = validate_agent_steps(plan)
    assert ok is True, err


def test_test_passes_rule_accepted_for_coding_step():
    """动作 B 第一级可达性：test_passes 是合法验收类型（此前会被白名单打回）。"""
    plan = {"segments": [{"steps": [
        _coding_step("实现 load_knowledge 方法", ["def load_knowledge"], description="实现 load_knowledge"),
    ]}]}
    step = plan["segments"][0]["steps"][0]
    step["acceptance"].append(
        {"type": "test_passes", "suite": "pytest", "test_path": "tests/test_knowledge.py"}
    )
    ok, err = validate_agent_steps(plan)
    assert ok, err


def test_designer_prompt_offers_test_passes():
    """提示词必须教设计器生成 test_passes（否则功能不可达）。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "python" / "designer" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "test_passes" in text
    assert "行为对不对由 pytest 判定" in text


def test_designer_prompt_has_rules_and_unified_language():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "python" / "designer" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "验收-指令一致性（强约束" in text
    assert "零交集会被直接打回" in text
    assert "宽松验收优先" in text
    assert "语言统一" in text
    # 提示词标记语言统一：emoji 与 [OK]/[WARN] 已移除
    assert "📁" not in text and "⚠️" not in text and "🔧" not in text and "🎯" not in text
    assert "[OK]" not in text and "[WARN]" not in text
    assert "【coding_agent 步骤契约" in text and "【输出要求】" in text
