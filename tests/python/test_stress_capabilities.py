# -*- coding: utf-8 -*-
"""
能力强度测试（离线、可重复、有计时指标）。

覆盖：usage_tracker 并发记账（1000 笔）、code_context 大工作区（300 文件）
索引/地图/并发检索、remember 批量、super_search 多引擎合并与故障风暴、
lint_gate 批量矩阵、command_runner 空输出、P2 注入预算压测。

计时指标以 `STRESS|名称|耗时s|附加` 格式输出（pytest -s 可见）。
"""
import concurrent.futures
import json
import time
from pathlib import Path

import pytest

import pytest as _pytest

from tools import command_runner as cr_mod
from tools import super_search as ss
from tools.code_context import CodeContext
from tools.lint_gate import lint_python
from tools.text_editor import TextEditor
from utils import usage_tracker
from utils.usage_tracker import clear_context, get_summary, plan_total_tokens, record_usage, set_context

METRICS = []


def _metric(name, seconds, extra=""):
    METRICS.append((name, seconds, extra))
    print(f"STRESS|{name}|{seconds:.2f}s|{extra}", flush=True)


@pytest.fixture(scope="module")
def big_ws(tmp_path_factory):
    """模块级大工作区：300 个文本文件（200 py + 50 md + 50 js），供索引/地图/检索压测。

    module 级手动补丁（monkeypatch 是 function 级，不能进 module 夹具），finally 恢复。
    """
    import tools.workspace as _ws_mod

    root = tmp_path_factory.mktemp("stress_ws")
    (root / "app").mkdir()
    (root / "docs").mkdir()
    (root / "web").mkdir()
    for i in range(200):
        (root / "app" / f"module_{i:03d}.py").write_text(
            f'"""模块 {i}"""\n'
            f'class Service{i}:\n'
            f'    """服务 {i}"""\n'
            f'    def handle_{i}(self, payload):\n'
            f'        return payload * {i}\n'
            f'\n'
            f'def util_{i}(value):\n'
            f'    return Service{i}().handle_{i}(value)\n',
            encoding="utf-8",
        )
    for i in range(50):
        (root / "docs" / f"guide_{i:02d}.md").write_text(
            f"# 指南 {i}\n\n## 部署 步骤 {i}\n\n构建并部署 service-{i}。\n",
            encoding="utf-8",
        )
    for i in range(50):
        (root / "web" / f"page_{i:02d}.ts").write_text(
            f"export class Page{i} {{\n  render() {{ return {i}; }}\n}}\n",
            encoding="utf-8",
        )
    _original = _ws_mod.resolve_workspace_root
    _ws_mod.resolve_workspace_root = lambda: root
    try:
        yield root
    finally:
        _ws_mod.resolve_workspace_root = _original


# ===== 1. usage_tracker 并发记账 =====

def test_stress_usage_concurrent_writes(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    clear_context()
    THREADS, PER_THREAD = 20, 50

    def worker(worker_id):
        for j in range(PER_THREAD):
            record_usage("prov", f"model-{worker_id % 4}",
                         {"prompt_tokens": 2, "completion_tokens": 1},
                         phase="stress", plan_id=f"plan-{worker_id}")

    set_context(phase="stress")
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [pool.submit(worker, w) for w in range(THREADS)]
        for f in futures:
            f.result(timeout=60)
    elapsed = time.perf_counter() - t0

    total_rows = THREADS * PER_THREAD
    summary = get_summary(limit=1)
    assert summary["calls"] == total_rows, f"并发丢账: {summary['calls']} != {total_rows}"
    assert summary["total_tokens"] == total_rows * 3
    # 并发读不崩
    for w in range(THREADS):
        assert plan_total_tokens(f"plan-{w}") == PER_THREAD * 3
    _metric("usage_tracker 并发记账", elapsed, f"{total_rows} 行 / {THREADS} 线程")
    clear_context()


def test_stress_usage_garbage_storm(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    garbage = [None, {}, {"prompt_tokens": "abc"}, object(), "not-a-dict", 42]
    t0 = time.perf_counter()
    for i in range(120):
        record_usage("p", "m", garbage[i % len(garbage)])
    elapsed = time.perf_counter() - t0
    summary = get_summary()
    assert summary["calls"] == 120  # 全部落账、零异常
    _metric("usage_tracker 垃圾输入风暴", elapsed, "120 次调用 0 异常")


# ===== 2. code_context 大工作区 =====

def test_stress_reindex_300_files(big_ws):
    t0 = time.perf_counter()
    result = CodeContext.reindex()
    elapsed = time.perf_counter() - t0
    assert result["success"] is True
    assert result["indexed_total"] == 300
    _metric("reindex 300 文件（首建）", elapsed, f"{result['indexed_total']} 文件, 引擎 {result['engine']}")


def test_stress_reindex_incremental(big_ws):
    t0 = time.perf_counter()
    result = CodeContext.reindex()
    elapsed = time.perf_counter() - t0
    assert result["updated"] == 0 and result["unchanged"] == 300
    _metric("reindex 300 文件（增量无变化）", elapsed, "0 更新")


def test_stress_repo_map_budget(big_ws):
    t0 = time.perf_counter()
    result = CodeContext.repo_map(max_chars=4000)
    elapsed = time.perf_counter() - t0
    assert result["success"] is True
    assert len(result["map"]) <= 4000 + 120
    assert result["shown_files"] < result["files"]
    _metric("repo_map 300 文件 / 4000 预算", elapsed,
            f"展示 {result['shown_files']}/{result['files']} 文件, 输出 {len(result['map'])} 字符")


def test_stress_concurrent_searches(big_ws):
    queries = [f"Service{i}" for i in range(0, 100, 5)] + [f"util{i}" for i in range(0, 50, 5)] + ["部署"]
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda q: CodeContext.search(q, max_results=5), queries))
    elapsed = time.perf_counter() - t0
    assert all(r["success"] for r in results)
    assert any(any("app/module_" in x["path"] for x in r["results"]) for r in results[:10])
    _metric(f"并发检索 {len(queries)} 查询 / 8 线程", elapsed, "全部成功且命中符号文件")


def test_stress_remember_50_notes(big_ws):
    t0 = time.perf_counter()
    for i in range(50):
        r = CodeContext.remember(f"stress-note-{i}：部署 {i} 号服务的注意事项与回滚清单。", title=f"压力笔记{i}")
        assert r["success"] is True
    elapsed = time.perf_counter() - t0
    _metric("remember 批量 50 条", elapsed, "写入+即时索引")


def test_stress_memory_block_under_200_notes(big_ws):
    from knowledge.injection import build_design_memory_block

    t0 = time.perf_counter()
    block = build_design_memory_block("部署 服务 回滚 注意事项", max_chars=2000)
    elapsed = time.perf_counter() - t0
    assert block is not None
    assert len(block) <= 2000
    _metric("P2 记忆块（200 笔记工作区）", elapsed, f"输出 {len(block)} 字符 ≤ 2000")


def test_stress_repo_map_block_injection(big_ws):
    from knowledge.injection import build_repo_map_block

    t0 = time.perf_counter()
    block = build_repo_map_block(2500)
    elapsed = time.perf_counter() - t0
    assert block is not None and len(block) <= 2500 + 120
    _metric("P2 仓库地图块注入", elapsed, f"输出 {len(block)} 字符")


# ===== 3. super_search 合并与故障风暴 =====

def _fake_engine_items(engine, n):
    items = []
    for i in range(n):
        noise = f"?utm_source={engine}&fbclid=x{i}" if i % 2 else f"#frag{i}"
        host = "www." if i % 3 == 0 else ""
        items.append({
            "title": f"{engine} 结果 {i}",
            "url": f"https://{host}example.com/page{i}{noise}",
            "snippet": f"{engine} 的第 {i} 条摘要，包含关键词 langgraph 与 checkpoint。",
        })
    return items


def test_stress_search_merge_five_engines(monkeypatch):
    engines = ["duckduckgo", "bing", "tavily", "brave", "serper"]
    for name in engines:
        monkeypatch.setitem(ss._ENGINE_FUNCS, name,
                            lambda q, n, t, _n=name: _fake_engine_items(_n, 30))
    t0 = time.perf_counter()
    result = ss.SuperSearch.search("langgraph checkpoint", max_results=30, engines=list(engines))
    elapsed = time.perf_counter() - t0
    assert result["success"] is True
    # 5 引擎 × 30 条、大量噪声 URL → 归一化后应收敛到 30 个唯一页面
    assert len(result["results"]) == 30
    top = result["results"][0]
    assert set(top["engines"]) == set(engines)  # 全引擎共识排第一
    _metric("super_search 5 引擎 × 30 条合并去重", elapsed,
            f"30 唯一结果, 共识 Top-1 引擎数 {len(top['engines'])}")


def test_stress_search_all_engines_fail_visible(monkeypatch):
    def boom(q, n, t):
        raise ss.EngineError("HTTP 403")

    for name in list(ss._ENGINE_FUNCS):
        monkeypatch.setitem(ss._ENGINE_FUNCS, name, boom)
    t0 = time.perf_counter()
    result = ss.SuperSearch.search("任何查询", engines=list(ss._ENGINE_FUNCS))
    elapsed = time.perf_counter() - t0
    assert result["success"] is False
    assert len(result["engines_failed"]) == len(ss._ENGINE_FUNCS)
    _metric("super_search 全引擎故障风暴（失败必须可见）", elapsed,
           f"{len(result['engines_failed'])} 个引擎错误如实上报")


def test_stress_search_partial_engine_failure(monkeypatch):
    def boom(q, n, t):
        raise ss.EngineError("HTTP 403")

    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", boom)
    monkeypatch.setitem(ss._ENGINE_FUNCS, "bing",
                        lambda q, n, t: [{"title": "OK", "url": "https://ok.com/", "snippet": "s"}])
    result = ss.SuperSearch.search("q")
    assert result["success"] is True and len(result["results"]) == 1
    assert result["engines_failed"]["duckduckgo"] == "HTTP 403"


# ===== 4. lint_gate 批量矩阵 =====

def test_stress_lint_gate_matrix(tmp_path):
    valid_snippets = [
        "def f():\n    return 1\n",
        "class A:\n    pass\n",
        "x = [i for i in range(10)]\n",
        "import os\nprint(os.getcwd())\n",
        "async def g():\n    await g()\n",
        "try:\n    1 / 0\nexcept ZeroDivisionError:\n    pass\n",
    ]
    invalid_snippets = [
        "def broken(:\n",
        "class B(\n",
        "x = = 5\n",
        "if True\n    pass\n",
        "def f():\nreturn 1\n    ",
        "print('unterminated\n",
        "lambda x: x = 5\n",
        "global global global = 1\n",
        "def f(:\n    pass\n",
        "return outside\n",
    ]
    files = []
    for i in range(60):
        is_valid = i % 2 == 0
        content = valid_snippets[(i // 2) % len(valid_snippets)] if is_valid else \
            invalid_snippets[(i // 2) % len(invalid_snippets)]
        p = tmp_path / f"case_{i:02d}.py"
        p.write_text(content, encoding="utf-8")
        files.append((p, is_valid))

    t0 = time.perf_counter()
    correct = 0
    for p, is_valid in files:
        old = p.read_text(encoding="utf-8")
        gate = lint_python(str(p), old, None)  # 模拟"原样重写"场景
        if gate.allowed == is_valid:
            correct += 1
    elapsed = time.perf_counter() - t0
    assert correct == 60, f"门禁判定错误 {60 - correct}/60"
    _metric("lint_gate 60 文件判定矩阵", elapsed, f"60/60 判定正确")


def test_stress_text_editor_gate_under_fire(tmp_path):
    target = tmp_path / "live.py"
    target.write_text("value = 1\n", encoding="utf-8")
    t0 = time.perf_counter()
    blocked = accepted = 0
    for i in range(30):
        if i % 3 == 0:
            r = TextEditor.execute("append_to_file", {"path": str(target), "content": f"\nbad_{i} = (\n"})
            assert r["success"] is False and r["blocked"] is True
            blocked += 1
        else:
            r = TextEditor.execute("append_to_file", {"path": str(target), "content": f"\nok_{i} = {i}\n"})
            assert r["success"] is True
            accepted += 1
    elapsed = time.perf_counter() - t0
    assert "bad_0" not in target.read_text(encoding="utf-8")  # 坏内容从未落盘
    assert "ok_2" in target.read_text(encoding="utf-8")
    _metric("text_editor 门禁连射 30 次", elapsed, f"{blocked} 拦截 / {accepted} 放行")


# ===== 5. command_runner 空输出批量 =====

def test_stress_command_runner_empty_output(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_mod, "_resolve_workspace_root", lambda: tmp_path)
    monkeypatch.delenv("EAS_SANDBOX", raising=False)
    t0 = time.perf_counter()
    for i in range(10):
        r = CommandRunner_Run("exit 0")
        assert r["success"] is True and r["empty_output"] is True
    elapsed = time.perf_counter() - t0
    _metric("command_runner 空输出确认 × 10", elapsed, "全部显式确认")


def CommandRunner_Run(command, timeout=30):
    return cr_mod.CommandRunner.run_command(command=command, shell=True, timeout=timeout)


# ===== 汇总 =====

def test_stress_metrics_summary():
    assert len(METRICS) >= 10, "强度指标数量异常"
    lines = ["=== STRESS 汇总 ==="] + [f"{n}: {s:.2f}s {e}" for n, s, e in METRICS]
    print("\n".join(lines), flush=True)
