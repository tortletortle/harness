"""
代码上下文与知识检索工具（code_context）

借鉴开源实践实现的工作区内上下文与记忆能力：
- repo_map：Aider 式"仓库地图"——文件树 + 关键符号（类/函数/路由/标题）
  + 按引用频次的图排序，在字符预算内输出（Aider 用 tree-sitter + PageRank，
  本实现用 stdlib ast/正则做轻量版，零新依赖）。
- search / reindex：SQLite FTS5 全文检索（无向量、零部署；FTS5 不可用时
  自动退回 LIKE 扫描）。参考 CrewAI 长期记忆（SQLite）与 SQLite FTS5
  本地检索模式。
- remember：把经验/笔记写入 data/knowledge/notes/*.md 并入索引，形成
  跨任务的长期记忆（LangGraph "长期记忆与 checkpoint 分离"的思路）。

遵循 docs/ERROR_HANDLING.md 工具层约定：永不抛异常，统一返回
{"success": bool, "message": str, ...}。
"""

import ast
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools import workspace as workspace_mod

_DEFAULT_MAX_RESULTS = 8
_MAX_RESULTS_LIMIT = 30
_DEFAULT_MAP_CHARS = 4000
_MAP_CHARS_LIMIT = 20000
_FILE_SIZE_CAP = 512 * 1024  # 单文件入索引上限 512KB
_INDEX_FILE_CAP = 4000       # 索引文件数上限
_MAP_FILE_CAP = 1500         # repo_map 扫描文件数上限
_CHUNK_TARGET_CHARS = 800

_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", "target", "htmlcov", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".next", "coverage", "logs", ".openclaw", ".zcode",
    ".mimosa", ".github", ".idea", ".vscode", "x",
}
_TEXT_EXTS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".less",
    ".html", ".htm", ".rs", ".sql", ".sh", ".bat", ".ps1", ".svg", ".xml",
}
_SKIP_FILE_NAMES = {"api_token", "package-lock.json", "poetry.lock"}
_BINARY_SIGS = (b"\x00\x00", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8\xff")

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]{3,}")
_SQL_IDENT_RE = re.compile(r"[^A-Za-z_0-9]")


def _workspace_root() -> Path:
    return workspace_mod.resolve_workspace_root()


def _index_db_path() -> Path:
    return _workspace_root() / "data" / "knowledge" / "fts.db"


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 文件遍历
# ---------------------------------------------------------------------------

def _should_prune_dir(root: Path, dirpath: Path, name: str) -> bool:
    if name in _EXCLUDE_DIRS:
        return True
    try:
        rel = (dirpath / name).relative_to(root).as_posix()
    except ValueError:
        return True
    if rel == "data/knowledge":
        return False  # 知识笔记目录保持可索引
    if rel == "data" or rel.startswith("data/"):
        return True
    return False


def _should_index_file(root: Path, path: Path) -> bool:
    if path.name in _SKIP_FILE_NAMES or path.name.startswith(".env"):
        return False
    if path.suffix.lower() not in _TEXT_EXTS:
        return False
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    if (rel == "data" or rel.startswith("data/")) and not rel.startswith("data/knowledge/notes/"):
        return False
    return True


def _iter_text_files(root: Path, cap: int) -> List[Path]:
    """遍历工作区文本文件（排除依赖/构建/隐藏目录），返回相对路径列表。"""
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath_p = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _should_prune_dir(root, dirpath_p, d)]
        for name in sorted(filenames):
            path = dirpath_p / name
            if not _should_index_file(root, path):
                continue
            try:
                if path.stat().st_size > _FILE_SIZE_CAP:
                    continue
            except OSError:
                continue
            out.append(path)
            if len(out) >= cap:
                return out
    return out


def _looks_binary(raw: bytes) -> bool:
    return any(raw.startswith(sig) for sig in _BINARY_SIGS) and b"\x00" in raw[:1024]


def _read_text(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
        if _looks_binary(raw):
            return None
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16", errors="replace")
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 符号提取（repo_map）
# ---------------------------------------------------------------------------

def _py_signature(node) -> str:
    try:
        args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
        extra = len(node.args.posonlyargs) + len(node.args.args)
        tail = []
        if node.args.vararg:
            tail.append("*" + node.args.vararg.arg)
        if node.args.kwarg:
            tail.append("**" + node.args.kwarg.arg)
        if node.args.kwonlyargs:
            tail.extend("*" + a.arg for a in node.args.kwonlyargs)
        shown = args[:6]
        more = extra - len(shown) - sum(1 for _ in node.args.kwonlyargs)
        parts = shown + (["..."] if more > 0 else []) + tail
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(parts)})"
    except Exception:
        return f"def {getattr(node, 'name', '?')}()"


def _py_symbols(source: str) -> List[Tuple[int, str, List[str]]]:
    """返回 [(行号, 定义行文本, 子成员名列表)]（仅顶层）。"""
    try:
        tree = ast.parse(source)
    except Exception:
        return []
    out: List[Tuple[int, str, List[str]]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ][:8]
            bases = ""
            if node.bases:
                try:
                    bases = "(" + ", ".join(ast.unparse(b) for b in node.bases[:2]) + ")"
                except Exception:
                    bases = "(...)"
            out.append((node.lineno, f"class {node.name}{bases}:", methods))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.lineno, _py_signature(node), []))
    return out


_JS_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", re.M)
_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)
_JS_ARROW_RE = re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", re.M)
_RS_FN_RE = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)", re.M)
_RS_TYPE_RE = re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][\w]*)", re.M)
_MD_HEAD_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.M)


def _generic_symbols(rel: str, source: str) -> List[Tuple[int, str, List[str]]]:
    """非 Python 文件的轻量符号提取（JS/TS/Rust/Markdown）。"""
    ext = Path(rel).suffix.lower()
    out: List[Tuple[int, str, List[str]]] = []
    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        for m in _JS_CLASS_RE.finditer(source):
            out.append((source[: m.start()].count("\n") + 1, f"class {m.group(1)}", []))
        for m in _JS_FUNC_RE.finditer(source):
            out.append((source[: m.start()].count("\n") + 1, f"function {m.group(1)}()", []))
        for m in _JS_ARROW_RE.finditer(source):
            out.append((source[: m.start()].count("\n") + 1, f"const {m.group(1)} = (...) =>", []))
    elif ext == ".rs":
        for m in _RS_TYPE_RE.finditer(source):
            out.append((source[: m.start()].count("\n") + 1, f"type {m.group(1)}", []))
        for m in _RS_FN_RE.finditer(source):
            out.append((source[: m.start()].count("\n") + 1, f"fn {m.group(1)}()", []))
    elif ext == ".md":
        for m in _MD_HEAD_RE.finditer(source):
            level = len(m.group(1))
            if level <= 3:
                out.append((source[: m.start()].count("\n") + 1, f"{'  ' * (level - 1)}# {m.group(2).strip()}", []))
    return out


def _extract_symbols(rel: str, source: str) -> List[Tuple[int, str, List[str]]]:
    if Path(rel).suffix.lower() == ".py":
        return _py_symbols(source)
    return _generic_symbols(rel, source)


# ---------------------------------------------------------------------------
# 索引（FTS5 / LIKE 回退）
# ---------------------------------------------------------------------------

def _open_db() -> sqlite3.Connection:
    db_path = _index_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> bool:
    """建表；返回是否使用 FTS5。"""
    use_fts = _fts5_available(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "path TEXT PRIMARY KEY, mtime REAL, size INTEGER, hash TEXT)"
    )
    if use_fts:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "path UNINDEXED, chunk_no UNINDEXED, first_line UNINDEXED, text)"
        )
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "path TEXT, chunk_no INTEGER, first_line INTEGER, text TEXT)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_text ON chunks(text)")
    conn.commit()
    return use_fts


def _delete_file_chunks(conn: sqlite3.Connection, use_fts: bool, path: str) -> None:
    conn.execute("DELETE FROM chunks WHERE path = ?", (path,))


def _chunk_text(text: str) -> List[Tuple[int, str]]:
    """按行切块，目标 ~800 字符；返回 [(起始行号(1-based), 文本)]。"""
    lines = text.splitlines()
    chunks: List[Tuple[int, str]] = []
    buf: List[str] = []
    start_line = 1
    size = 0
    for i, line in enumerate(lines, start=1):
        buf.append(line)
        size += len(line) + 1
        if size >= _CHUNK_TARGET_CHARS:
            chunks.append((start_line, "\n".join(buf)))
            buf, size, start_line = [], 0, i + 1
    if buf or not chunks:
        chunks.append((start_line, "\n".join(buf)))
    return chunks


def _index_file(conn: sqlite3.Connection, use_fts: bool, root: Path, path: Path) -> bool:
    """索引单个文件；内容不可读返回 False。"""
    rel = path.relative_to(root).as_posix()
    text = _read_text(path)
    if text is None:
        return False
    stat = path.stat()
    content_hash = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
    row = conn.execute("SELECT hash FROM files WHERE path = ?", (rel,)).fetchone()
    if row and row[0] == content_hash:
        return False  # 未变化
    _delete_file_chunks(conn, use_fts, rel)
    for chunk_no, (first_line, chunk) in enumerate(_chunk_text(text), start=1):
        conn.execute(
            "INSERT INTO chunks (path, chunk_no, first_line, text) VALUES (?, ?, ?, ?)",
            (rel, chunk_no, first_line, chunk),
        )
    conn.execute(
        "INSERT INTO files (path, mtime, size, hash) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, hash=excluded.hash",
        (rel, stat.st_mtime, stat.st_size, content_hash),
    )
    return True


# ---------------------------------------------------------------------------
# 工具主类
# ---------------------------------------------------------------------------

def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class CodeContext:
    """代码上下文与知识检索工具（repo_map / search / reindex / remember）"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        return {
            "tool_id": "code_context",
            "name": "代码上下文与知识检索工具",
            "description": "工作区仓库地图（关键符号+引用排序，Aider repo map 轻量版）、"
                           "SQLite FTS5 全文检索、知识笔记长期记忆",
            "category": "search",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "repo_map",
                        "description": "生成工作区代码地图：文件+关键符号（类/函数/标题）按引用频次排名，字符预算内输出",
                        "parameters": {
                            "max_chars": "输出字符预算，默认 4000，上限 20000",
                            "paths": "可选，限定子目录/文件（列表或逗号分隔）"
                        }
                    },
                    {
                        "name": "search",
                        "description": "全文检索工作区文件与知识笔记（FTS5，不可用时 LIKE 回退），返回 文件/行号/摘录",
                        "parameters": {
                            "query": "检索关键词（必填）",
                            "max_results": "返回条数，默认 8，上限 30"
                        }
                    },
                    {
                        "name": "reindex",
                        "description": "增量重建全文索引（按内容哈希，只处理变化文件）",
                        "parameters": {"force": "可选，true 时全量重建"}
                    },
                    {
                        "name": "remember",
                        "description": "把经验/结论写入知识笔记（data/knowledge/notes/*.md）并入索引，跨任务可检索",
                        "parameters": {
                            "note": "笔记内容（必填）",
                            "title": "可选标题，默认取内容前段"
                        }
                    }
                ]
            }
        }

    # -- repo_map -----------------------------------------------------------

    @staticmethod
    def repo_map(max_chars: Any = _DEFAULT_MAP_CHARS, paths: Any = None) -> Dict[str, Any]:
        try:
            root = _workspace_root()
            budget = _clamp_int(max_chars, _DEFAULT_MAP_CHARS, 500, _MAP_CHARS_LIMIT)
            filters = _normalize_paths(paths)
            files = _iter_text_files(root, _MAP_FILE_CAP)
            if filters:
                files = [p for p in files if _match_filters(p, root, filters)]
            sources: Dict[str, str] = {}
            symbols: Dict[str, List[Tuple[int, str, List[str]]]] = {}
            for path in files:
                rel = path.relative_to(root).as_posix()
                source = _read_text(path)
                if source is None:
                    continue
                sources[rel] = source
                symbols[rel] = _extract_symbols(rel, source)
            if not sources:
                return {"success": True, "message": "工作区未发现可映射的文本文件", "map": "", "files": 0}

            # 引用频次：对每个文件做一次标识符计数，汇总到全局（近似引用图排名）
            token_counter: Counter = Counter()
            for source in sources.values():
                token_counter.update(m.group(0) for m in _WORD_RE.finditer(source))

            def file_score(rel: str) -> int:
                score = 0
                for _, decl, methods in symbols.get(rel, []):
                    names = re.findall(r"[A-Za-z_][\w]*", decl)
                    score += sum(token_counter.get(n, 0) for n in names)
                    score += sum(token_counter.get(m, 0) for m in methods)
                return score

            ranked = sorted(sources.keys(), key=file_score, reverse=True)
            lines: List[str] = [f"workspace: {root.name}/  ({len(ranked)} files, 预算 {budget} 字符)"]
            used = len(lines[0])
            shown_files = 0
            for rel in ranked:
                header = f"{rel}:"
                sym_lines: List[str] = []
                used_tmp = used + len(header) + 1
                for lineno, decl, methods in sorted(symbols[rel], key=lambda s: s[0]):
                    line = f"  {decl}  #L{lineno}"
                    if used_tmp + len(line) + 1 > budget:
                        break
                    sym_lines.append(line)
                    used_tmp += len(line) + 1
                    for mth in methods:
                        mline = f"    {mth}"
                        if used_tmp + len(mline) + 1 > budget:
                            break
                        sym_lines.append(mline)
                        used_tmp += len(mline) + 1
                if not sym_lines:
                    continue  # 一个符号都放不下：跳过，避免输出光秃秃的路径行
                lines.append(header)
                lines.extend(sym_lines)
                used = used_tmp
                shown_files += 1
            if shown_files < len(ranked):
                lines.append(f"... 其余 {len(ranked) - shown_files} 个文件未展示（预算已用完，可用 paths 参数缩小范围）")
            return {
                "success": True,
                "message": f"仓库地图完成：{shown_files}/{len(ranked)} 个文件，{len(lines) - 1} 行",
                "map": "\n".join(lines),
                "files": len(ranked),
                "shown_files": shown_files,
            }
        except Exception as e:
            return {"success": False, "message": f"仓库地图失败: {e}", "error": str(e)}

    # -- search / reindex ---------------------------------------------------

    @staticmethod
    def search(query: str, max_results: Any = _DEFAULT_MAX_RESULTS) -> Dict[str, Any]:
        try:
            query = (query or "").strip()
            if not query:
                return {"success": False, "message": "query 不能为空", "error": "Invalid input"}
            limit = _clamp_int(max_results, _DEFAULT_MAX_RESULTS, 1, _MAX_RESULTS_LIMIT)
            conn = _open_db()
            try:
                use_fts = _ensure_schema(conn)
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                if total == 0:
                    # 首次调用自动建索引
                    stats = CodeContext.reindex()
                    total = stats.get("indexed_total", 0)
                results = _run_search(conn, use_fts, query, limit)
                if not results:
                    return {
                        "success": True,
                        "message": f"未命中（已索引 {total} 个文件）。可用 reindex 刷新索引，或换个关键词。",
                        "query": query, "results": [], "indexed_files": total,
                    }
                return {
                    "success": True,
                    "message": f"检索完成：{len(results)} 条命中（已索引 {total} 个文件，引擎: {'fts5' if use_fts else 'like'}）",
                    "query": query,
                    "results": results,
                    "indexed_files": total,
                }
            finally:
                conn.close()
        except Exception as e:
            return {"success": False, "message": f"检索失败: {e}", "error": str(e)}

    @staticmethod
    def reindex(force: Any = False) -> Dict[str, Any]:
        try:
            root = _workspace_root()
            force = force is True or str(force).lower() == "true"
            conn = _open_db()
            try:
                use_fts = _ensure_schema(conn)
                if force:
                    conn.execute("DELETE FROM chunks")
                    conn.execute("DELETE FROM files")
                    conn.commit()
                files = _iter_text_files(root, _INDEX_FILE_CAP)
                indexed = updated = unchanged = skipped = 0
                seen = set()
                for path in files:
                    rel = path.relative_to(root).as_posix()
                    seen.add(rel)
                    text_probe = _read_text(path)
                    if text_probe is None:
                        skipped += 1
                        continue
                    changed = _index_file(conn, use_fts, root, path)
                    if changed:
                        updated += 1
                    else:
                        unchanged += 1
                    indexed += 1
                stale = [
                    r[0] for r in conn.execute("SELECT path FROM files").fetchall()
                    if r[0] not in seen
                ]
                for rel in stale:
                    _delete_file_chunks(conn, use_fts, rel)
                    conn.execute("DELETE FROM files WHERE path = ?", (rel,))
                conn.commit()
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                return {
                    "success": True,
                    "message": f"索引完成：共 {total} 个文件（本次新增/更新 {updated}，未变 {unchanged}，"
                               f"移除 {len(stale)}，跳过 {skipped}，引擎: {'fts5' if use_fts else 'like'}）",
                    "indexed_total": total,
                    "updated": updated,
                    "unchanged": unchanged,
                    "removed": len(stale),
                    "skipped": skipped,
                    "engine": "fts5" if use_fts else "like",
                }
            finally:
                conn.close()
        except Exception as e:
            return {"success": False, "message": f"索引失败: {e}", "error": str(e)}

    # -- remember -----------------------------------------------------------

    @staticmethod
    def remember(note: str, title: str = "") -> Dict[str, Any]:
        try:
            note = (note or "").strip()
            if not note:
                return {"success": False, "message": "note 不能为空", "error": "Invalid input"}
            title = (title or "").strip() or note.splitlines()[0][:40]
            slug = _SQL_IDENT_RE.sub("-", title.lower())[:48].strip("-") or "note"
            root = _workspace_root()
            notes_dir = root / "data" / "knowledge" / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = notes_dir / f"{stamp}-{slug}.md"
            body = (
                f"# {title}\n\n"
                f"- created: {datetime.now().isoformat(timespec='seconds')}\n"
                f"- source: code_context.remember\n\n"
                f"{note}\n"
            )
            path.write_text(body, encoding="utf-8")
            conn = _open_db()
            try:
                use_fts = _ensure_schema(conn)
                _index_file(conn, use_fts, root, path)
                conn.commit()
                engine = "fts5" if use_fts else "like"
            finally:
                conn.close()
            return {
                "success": True,
                "message": f"知识笔记已保存并索引: {path.relative_to(root).as_posix()}",
                "path": path.relative_to(root).as_posix(),
                "title": title,
                "engine": engine,
            }
        except Exception as e:
            return {"success": False, "message": f"笔记保存失败: {e}", "error": str(e)}

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        inputs = inputs or {}
        if instruction == "repo_map":
            return CodeContext.repo_map(
                max_chars=inputs.get("max_chars", _DEFAULT_MAP_CHARS),
                paths=inputs.get("paths"),
            )
        elif instruction == "search":
            return CodeContext.search(
                query=inputs.get("query", ""),
                max_results=inputs.get("max_results", _DEFAULT_MAX_RESULTS),
            )
        elif instruction == "reindex":
            return CodeContext.reindex(force=inputs.get("force", False))
        elif instruction == "remember":
            return CodeContext.remember(
                note=inputs.get("note", inputs.get("content", "")),
                title=inputs.get("title", ""),
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }


# ---------------------------------------------------------------------------
# 辅助：路径过滤与检索执行
# ---------------------------------------------------------------------------

def _normalize_paths(paths: Any) -> Optional[List[str]]:
    if paths is None:
        return None
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]
    if not isinstance(paths, (list, tuple)) or not paths:
        return None
    cleaned = []
    for p in paths:
        rel = str(p).strip().replace("\\", "/").strip("/")
        if rel:
            cleaned.append(rel)
    return cleaned or None


def _match_filters(path: Path, root: Path, filters: List[str]) -> bool:
    rel = path.relative_to(root).as_posix()
    return any(rel == f or rel.startswith(f + "/") or f in rel for f in filters)


def _build_fts_query(query: str) -> str:
    tokens = [t for t in re.findall(r"\w+", query) if len(t) >= 2]
    if not tokens:
        tokens = [query.strip()]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens[:12])


def _run_search(conn: sqlite3.Connection, use_fts: bool, query: str, limit: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if use_fts:
        fts_query = _build_fts_query(query)
        try:
            rows = conn.execute(
                "SELECT path, first_line, text, snippet(chunks, 3, '「', '」', '…', 16) AS snip, "
                "bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except Exception:
            rows = []
        for path, first_line, text, snip, _ in rows:
            offset = max(0, snip.find("「"))
            line = first_line + text[: max(offset, 0)].count("\n") if offset >= 0 else first_line
            results.append({"path": path, "line": line, "excerpt": snip.replace("\n", " ")[:300]})
    if not results:
        # LIKE 回退（或 FTS5 无命中时的兜底扫描）
        tokens = [t for t in re.findall(r"\w+", query) if len(t) >= 2][:6] or [query.strip()]
        seen = set()
        for token in tokens:
            if len(results) >= limit:
                break
            pattern = f"%{token}%"
            rows = conn.execute(
                "SELECT path, first_line, text FROM chunks WHERE text LIKE ? LIMIT ?", (pattern, limit * 3)
            ).fetchall()
            for path, first_line, text in rows:
                if len(results) >= limit:
                    break
                key = (path, first_line)
                if key in seen:
                    continue
                seen.add(key)
                lower = text.lower().find(token.lower())
                start = max(0, lower - 60)
                excerpt = text[start: start + 180].replace("\n", " ")
                line = first_line + text[: max(lower, 0)].count("\n")
                results.append({"path": path, "line": line, "excerpt": excerpt[:300]})
    return results
