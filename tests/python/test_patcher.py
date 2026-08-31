# -*- coding: utf-8 -*-
"""patcher 单测：search/replace 补丁块的解析与应用（削接缝动作 A 地基）。"""
import sys

sys.path.insert(0, "python")

from patcher import apply_patches, error_feedback_text, parse_blocks

OLD = "def create_user(name, email):\n    return User(name, email)\n"


def _patch(search, replace):
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def test_single_block_replaces_exact():
    p = _patch("def create_user(name, email):", "def create_user(user_id, name, email):")
    r = apply_patches(OLD, p)
    assert r.ok and r.applied == 1
    assert "def create_user(user_id, name, email):" in r.content
    assert "user_id, name, email)" in r.content


def test_new_file_empty_search():
    r = apply_patches(None, _patch("", "hello\nworld"))
    assert r.ok and r.content == "hello\nworld"


def test_new_file_allowed_when_current_empty_string():
    r = apply_patches("", _patch("", "x"))
    assert r.ok and r.content == "x"


def test_new_file_conflict_when_exists():
    r = apply_patches(OLD, _patch("", "x"))
    assert not r.ok and r.error.kind == "file_exists_conflict"


def test_not_found_error_has_hint():
    r = apply_patches(OLD, _patch("def missing():", "x"))
    assert not r.ok and r.error.kind == "not_found"
    assert "def missing():" in r.error.hint


def test_ambiguous_error_reports_count():
    r = apply_patches("alpha\nalpha\n", _patch("alpha", "beta"))
    assert not r.ok and r.error.kind == "ambiguous"
    assert "2" in r.error.message


def test_multiple_blocks_sequential():
    cur = "a = 1\nb = 2\nc = 3\n"
    p = _patch("a = 1", "a = 10") + "\n\n" + _patch("c = 3", "c = 30")
    r = apply_patches(cur, p)
    assert r.ok and r.applied == 2
    assert "a = 10" in r.content and "c = 30" in r.content and "b = 2" in r.content


def test_crlf_normalized_match_multiline_search():
    """SEARCH 跨行（含换行）时，CRLF 文档归一为 LF 后命中替换。"""
    cur = "def foo():\r\n    pass\r\n"
    p = _patch("def foo():\n    pass", "def bar():\n    pass")
    r = apply_patches(cur, p)
    assert r.ok and "def bar():" in r.content and "\r\n" not in r.content


def test_crlf_preserved_for_direct_match():
    """SEARCH 单行可直接命中时不强制归一，保留原文档行尾。"""
    cur = "def foo():\r\n    pass\r\n"
    r = apply_patches(cur, _patch("def foo():", "def bar():"))
    assert r.ok and "def bar():" in r.content and "\r\n" in r.content


def test_delete_via_empty_replace():
    cur = "keep\n\ndelete me\nkeep2\n"
    r = apply_patches(cur, _patch("delete me", ""))
    assert r.ok and "delete me" not in r.content and "keep2" in r.content


def test_ignores_surrounding_prose():
    p = "好的，下面是修改：\n\n" + _patch("a = 1", "a = 2") + "\n\n以上。"
    r = apply_patches("a = 1\n", p)
    assert r.ok and "a = 2" in r.content


def test_missing_divider_parse_error():
    blocks, err = parse_blocks("<<<<<<< SEARCH\nabc\n>>>>>>> REPLACE")
    assert blocks == [] and err is not None and err.kind == "parse_error"


def test_no_blocks_parse_error():
    blocks, err = parse_blocks("这个任务我不会做。")
    assert blocks == [] and err is not None and err.kind == "parse_error"


def test_code_fence_stripped():
    p = "```text\n" + _patch("a = 1", "a = 2") + "\n```"
    r = apply_patches("a = 1\n", p)
    assert r.ok and "a = 2" in r.content


def test_feedback_text_shape():
    r = apply_patches("abc\n", _patch("nope", "x"))
    fb = error_feedback_text(r)
    assert fb and "SEARCH" in fb and "nope" in fb


def test_feedback_text_none_on_success():
    r = apply_patches("a = 1\n", _patch("a = 1", "a = 2"))
    assert error_feedback_text(r) is None
