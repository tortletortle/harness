# -*- coding: utf-8 -*-
"""
code_context 回归测试：全部离线，使用临时工作区（monkeypatch workspace 根）。

覆盖：元数据契约、repo_map（符号提取/预算截断）、reindex 增量与排除规则、
FTS5 与 LIKE 回退双引擎检索、remember 笔记长期记忆、executor 指令推断、注册表契约。
"""
import json

import pytest

from tools import code_context as cc
from tools.code_context import CodeContext


PY_FILE = '''"""示例模块"""
import json


class UserModel:
    """用户模型"""

    def __init__(self, name):
        self.name = name

    def validate_email(self, email):
        return "@" in email


def create_user(name):
    return UserModel(name)
'''

MD_FILE = '''# 部署指南

## deploy 步骤

1. 构建镜像
2. 推送仓库

### 回滚

回滚到上一版本。
'''

JS_JUNK = 'function shouldNotAppear() { return "SHOULD-NOT-APPEAR"; }\n'


@pytest.fixture()
def tmp_ws(tmp_path, monkeypatch):
    """临时工作区：python 模块 + 文档 + 应被排除的 node_modules。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "app" / "models.py").write_text(PY_FILE, encoding="utf-8")
    (tmp_path / "app" / "service.py").write_text(
        "from app.models import UserModel\n\n\ndef run(name):\n    return UserModel(name).validate_email(f\"{name}@x.com\")\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(MD_FILE, encoding="utf-8")
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "junk.js").write_text(JS_JUNK, encoding="utf-8")
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    return tmp_path


# ===== 元数据与分发 =====

def test_metadata_contract():
    meta = CodeContext.get_metadata()
    assert meta["tool_id"] == "code_context"
    assert meta["category"] == "search"
    assert [i["name"] for i in meta["capabilities"]["instructions"]] == [
        "repo_map", "search", "reindex", "remember",
    ]


def test_execute_unknown_instruction():
    result = CodeContext.execute("nope", {})
    assert result["success"] is False
    assert result["error"] == "Invalid instruction"


# ===== repo_map =====

def test_repo_map_contains_symbols(tmp_ws):
    result = CodeContext.repo_map()
    assert result["success"] is True
    assert result["files"] >= 3
    assert "app/models.py:" in result["map"]
    assert "class UserModel" in result["map"]
    assert "def validate_email" in result["map"] or "validate_email" in result["map"]


def test_repo_map_respects_budget(tmp_ws):
    # 再加 4 个带标题的文档，让内容真实超出预算
    for i in range(4):
        (tmp_ws / "docs" / f"extra{i}.md").write_text(
            f"# extra {i}\n\n## section one {i}\n\nbody content here\n\n## section two {i}\n\nmore body\n",
            encoding="utf-8",
        )
    result = CodeContext.repo_map(max_chars=500)
    assert result["success"] is True
    assert len(result["map"]) <= 500 + 120  # 预算 + 溢出说明行
    assert result["shown_files"] < result["files"]
    assert "app/models.py" in result["map"]
    assert "预算已用完" in result["map"]


def test_repo_map_paths_filter(tmp_ws):
    result = CodeContext.repo_map(paths=["docs"])
    assert result["success"] is True
    assert "app/models.py" not in result["map"]
    assert "docs/guide.md" in result["map"]
    assert "部署指南" in result["map"] or "deploy" in result["map"]


# ===== reindex / search =====

def test_reindex_and_search(tmp_ws):
    stats = CodeContext.reindex()
    assert stats["success"] is True
    assert stats["indexed_total"] == 3  # models.py / service.py / guide.md
    assert (tmp_ws / "data" / "knowledge" / "fts.db").exists()

    result = CodeContext.search("validate_email")
    assert result["success"] is True
    paths = [r["path"] for r in result["results"]]
    assert "app/models.py" in paths
    top = result["results"][0]
    assert top["line"] >= 1 and top["excerpt"]


def test_reindex_incremental_no_changes(tmp_ws):
    CodeContext.reindex()
    again = CodeContext.reindex()
    assert again["success"] is True
    assert again["updated"] == 0
    assert again["unchanged"] == 3


def test_search_excludes_node_modules(tmp_ws):
    CodeContext.reindex()
    result = CodeContext.search("SHOULD-NOT-APPEAR")
    assert result["success"] is True
    assert result["results"] == []


def test_search_empty_query_rejected(tmp_ws):
    result = CodeContext.search("   ")
    assert result["success"] is False


def test_search_like_fallback_engine(tmp_ws, monkeypatch):
    monkeypatch.setattr(cc, "_fts5_available", lambda conn: False)
    CodeContext.reindex()
    result = CodeContext.search("回滚")
    assert result["success"] is True
    assert any(r["path"] == "docs/guide.md" for r in result["results"])


def test_search_auto_indexes_on_first_call(tmp_ws):
    # 未显式 reindex，直接 search 也应自动建索引并命中
    result = CodeContext.search("create_user")
    assert result["success"] is True
    assert any(r["path"] == "app/models.py" for r in result["results"])


# ===== remember =====

def test_remember_writes_and_searchable(tmp_ws):
    result = CodeContext.remember(
        "部署前必须运行 pytest 回归，避免把坏构建推上环境。关键字：release-checklist",
        title="发布检查单",
    )
    assert result["success"] is True
    note_path = tmp_ws / result["path"]
    assert note_path.exists()
    assert note_path.parent == tmp_ws / "data" / "knowledge" / "notes"

    found = CodeContext.search("release-checklist")
    assert found["success"] is True
    assert any(r["path"] == result["path"] for r in found["results"])


def test_remember_empty_note_rejected(tmp_ws):
    result = CodeContext.remember("   ")
    assert result["success"] is False


# ===== 集成：executor 指令推断 + 注册表 =====

def test_executor_infer_instruction_mapping():
    from executor import DefaultExecutor
    from llm_client import LLMClient

    executor = DefaultExecutor(llm_client=LLMClient(), model_config=None)
    assert executor._infer_instruction("code_context", {"query": "x"}) == "search"
    assert executor._infer_instruction("code_context", {"note": "记住这件事"}) == "remember"
    assert executor._infer_instruction("code_context", {"force": True}) == "reindex"
    assert executor._infer_instruction("code_context", {"max_chars": 4000}) == "repo_map"


def test_registry_contains_code_context():
    from tools import get_registry

    tool_ids = [t.get("tool_id") for t in get_registry().list_tools()]
    assert "code_context" in tool_ids
