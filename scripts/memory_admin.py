"""项目记忆运维工具（PROJECT_MEMORY.md / PROJECT_MEMORY-archive.md）。

用途：查看与手工整理项目工作记忆。自动滚动由 `tools.project_memory.save_entry()`
负责，本脚本只做人工运维：看统计、按条数裁剪归档、合并重复条目。

用法（任意目录执行均可；工作区根同运行时规则：EAS_WORKSPACE_ROOT 优先，否则仓库根）：
    python scripts/memory_admin.py stats                     # 统计（只读）
    python scripts/memory_admin.py stats --json               # 机器可读
    python scripts/memory_admin.py keep --entries 200         # dry-run：归档只保留最新 200 条
    python scripts/memory_admin.py keep --entries 200 --apply # 真正执行
    python scripts/memory_admin.py dedupe --target both --apply
    python scripts/memory_admin.py keep --entries 20 --target memory --apply

安全设计：
- 默认 dry-run，必须显式 --apply 才写文件；
- --apply 前自动把目标文件备份到 <工作区根>/out/memory-admin-backup-<名字>-<时间戳>；
- 只在**整条边界**增删，绝不产生半截条目（与 save_entry 的滚动手法一致）；
- `--target memory` 裁剪主文件时，被裁掉的条目**移动**到归档（不丢数据）；
  `--target archive` 裁剪归档才是真删除（这就是"清理"）；
- 不会创建归档文件之外的任何新文件，也不碰数据库、代码或配置。
"""

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "python"))
from tools import project_memory as pm  # noqa: E402
from tools.workspace import resolve_workspace_root  # noqa: E402

# 两种合法头部：机器写入 `## [YYYY-MM-DD HH:MM:SS] 标题`、人工整理 `## [YYYY-MM-DD] 标题`
# （后者是文件开头写明的索引约定，不能被当成异常条目）。
TS_PATTERN = re.compile(r"^## \[(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)\]\s*(.*)$")
TARGETS = ("memory", "archive", "both")


def _workspace_root(explicit: str | None) -> Path:
    return Path(explicit).resolve() if explicit else resolve_workspace_root()


def _path_for(root: Path, target: str) -> Path:
    name = pm.MEMORY_FILENAME if target == "memory" else pm.ARCHIVE_FILENAME
    return root / name


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _load(path: Path):
    preamble, entries = pm._split_entries(_read(path))
    return preamble, entries


def _timestamps(entry: str):
    match = TS_PATTERN.match(entry.split("\n", 1)[0])
    return (match.group(1), match.group(2).strip()) if match else ("", entry.split("\n", 1)[0][:60])


def _entry_identity(entry: str) -> str:
    """重复判定用的身份。

    带 dedupe_key 的条目（如 `执行摘要（计划 3f7fd751）`）按「标题 + 键」判定——这正是
    历史上同一计划被写两条摘要的情形（先 Failed 后 Completed），应当只保留最新一条；
    没有键的普通条目按「标题 + 正文」判定（忽略时间戳与空白差异），不做激进合并。
    """
    title, dedupe_key = pm._header_title_and_key(entry)
    if dedupe_key:
        return f"{title}||key:{dedupe_key}"
    _, _, body = entry.partition("\n")
    return f"{title}||body:{' '.join(body.split())}"


def _describe(root: Path, target: str) -> dict:
    path = _path_for(root, target)
    text = _read(path)
    preamble, entries = pm._split_entries(text)
    stamps = [_timestamps(e)[0] for e in entries if _timestamps(e)[0]]
    seen, dupes = {}, 0
    for entry in entries:
        key = _entry_identity(entry)
        if key in seen:
            dupes += 1
        seen[key] = True
    # 异常条目：头部不合规的条目，或前导区看着像被截断的旧条目（历史遗留的半截记录）。
    malformed = sum(1 for e in entries if not TS_PATTERN.match(e.split("\n", 1)[0]))
    stray = 1 if preamble.strip() and not preamble.lstrip().startswith("#") else 0
    half = malformed + stray
    return {
        "target": target,
        "path": str(path),
        "exists": path.exists(),
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "entries": len(entries),
        "duplicates": dupes,
        "half_entries": half,
        "oldest": stamps[0] if stamps else "",
        "newest": stamps[-1] if stamps else "",
    }


def cmd_stats(args) -> int:
    root = _workspace_root(args.workspace)
    limits = pm._limits()
    rows = [_describe(root, t) for t in TARGETS if t != "both"]
    payload = {
        "workspace_root": str(root),
        "limits": limits,
        "files": rows,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"工作区根: {root}")
    entry_limit = {"memory": limits["file_entries"], "archive": limits["archive_entries"]}
    char_limit = {"memory": limits["file_chars"], "archive": limits["archive_chars"]}
    for row in rows:
        target = row["target"]
        print(f"\n[{target}] {row['path']}")
        if not row["exists"]:
            print("  文件不存在（还没有内容）")
            continue
        print(f"  {row['entries']} 条 / {row['bytes']} 字节 / {row['chars']} 字符"
              f"（上限 {entry_limit[target]} 条 / {char_limit[target]} 字符）")
        if row["oldest"]:
            print(f"  最旧 {row['oldest']}  →  最新 {row['newest']}")
        if row["duplicates"]:
            print(f"  重复条目 {row['duplicates']} 条（可执行 dedupe 合并）")
        if row["half_entries"]:
            print(f"  异常条目 {row['half_entries']} 条（不以 '## [' 开头，建议人工检查）")
    print("\n提示：环境变量可调上限 —— EAS_PROJECT_MEMORY_MAX_ENTRIES / _MAX_CHARS / "
          "EAS_PROJECT_MEMORY_ARCHIVE_MAX_ENTRIES / _MAX_CHARS")
    return 0


def _backup(root: Path, path: Path) -> str:
    if not path.exists():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"memory-admin-backup-{path.stem}-{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    return str(dest)


def _write(path: Path, preamble: str, entries) -> None:
    path.write_text(preamble + "".join(entries), encoding="utf-8")


def _trim_file(root: Path, target: str, entries_limit: int, chars_limit: int | None, apply: bool):
    """按整条边界裁剪；memory 目标裁掉的条目移入归档（不丢数据）。"""
    path = _path_for(root, target)
    preamble, entries = _load(path)
    if not entries:
        return {"target": target, "kept": 0, "removed": 0, "moved": 0, "deleted": 0, "backup": ""}

    keep = pm._keep_newest(
        entries,
        chars_limit if chars_limit else 10 ** 9,
        entries_limit,
    )
    removed = entries[: len(entries) - len(keep)]

    result = {"target": target, "kept": len(keep), "removed": len(removed),
              "moved": 0, "deleted": 0, "backup": ""}
    if not removed:
        return result

    if target == "memory":
        result["moved"] = len(removed)
    else:
        result["deleted"] = len(removed)

    if not apply:
        return result

    if target == "memory":
        # 先归档再裁主文件：任何一步失败都不会丢数据。
        pm._append_archive(removed, time.strftime("%Y-%m-%d %H:%M:%S"))
        result["backup"] = _backup(root, path)
        _write(path, preamble, keep)
    else:
        result["backup"] = _backup(root, path)
        _write(path, preamble, keep)
    return result


def cmd_keep(args) -> int:
    root = _workspace_root(args.workspace)
    if args.entries <= 0:
        print("ERROR: --entries 必须为正整数")
        return 2
    targets = ("memory", "archive") if args.target == "both" else (args.target,)
    results = []
    for target in targets:
        if target == "memory":
            results.append(_trim_file(root, target, args.entries, args.chars, args.apply))
        else:
            # 归档字符上限没给就沿用运行时常量，避免把归档裁得比默认还狠。
            chars = args.chars if args.chars else None
            results.append(_trim_file(root, target, args.entries, chars, args.apply))

    print(f"工作区根: {root}    模式: {'APPLY（已写入）' if args.apply else 'dry-run（未写入）'}")
    for row in results:
        line = f"[{row['target']}] 保留 {row['kept']} 条"
        if row["removed"]:
            line += f"，移除 {row['removed']} 条（移入归档 {row['moved']} / 删除 {row['deleted']}）"
        else:
            line += "，无需裁剪"
        print("  " + line)
        if row["backup"]:
            print(f"  备份: {row['backup']}")
    if not args.apply and any(r["removed"] for r in results):
        print("\n以上为 dry-run 结果；确认无误后加 --apply 执行。")
    return 0


def _dedupe_file(root: Path, target: str, apply: bool):
    path = _path_for(root, target)
    preamble, entries = _load(path)
    kept, seen, dropped = [], {}, []
    for entry in entries:
        key = _entry_identity(entry)
        if key in seen:
            # 新条目替换旧条目，保留最新时间戳与内容。
            dropped.append(seen[key])
            kept[kept.index(seen[key])] = entry
            seen[key] = entry
        else:
            seen[key] = entry
            kept.append(entry)

    result = {"target": target, "before": len(entries), "after": len(kept),
              "removed": len(dropped), "backup": ""}
    if dropped and apply:
        result["backup"] = _backup(root, path)
        _write(path, preamble, kept)
    return result


def cmd_dedupe(args) -> int:
    root = _workspace_root(args.workspace)
    targets = ("memory", "archive") if args.target == "both" else (args.target,)
    print(f"工作区根: {root}    模式: {'APPLY（已写入）' if args.apply else 'dry-run（未写入）'}")
    for target in targets:
        row = _dedupe_file(root, target, args.apply)
        print(f"  [{row['target']}] {row['before']} 条 → {row['after']} 条"
              f"（合并重复 {row['removed']} 条）")
        if row["backup"]:
            print(f"  备份: {row['backup']}")
    if not args.apply:
        print("\n以上为 dry-run 结果；确认无误后加 --apply 执行。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="项目记忆运维（默认 dry-run）")
    parser.add_argument("--workspace", default="",
                        help="工作区根（默认 EAS_WORKSPACE_ROOT，否则仓库根）")
    sub = parser.add_subparsers(dest="command")

    p_stats = sub.add_parser("stats", help="显示两个记忆文件的统计（只读）")
    p_stats.add_argument("--json", action="store_true", help="输出 JSON")
    p_stats.set_defaults(func=cmd_stats)

    p_keep = sub.add_parser("keep", help="按条数/字符裁剪（默认只裁归档）")
    p_keep.add_argument("--entries", type=int, required=True, help="每个文件保留的条数")
    p_keep.add_argument("--chars", type=int, default=0, help="可选字符上限（0 表示不限制）")
    p_keep.add_argument("--target", choices=TARGETS, default="archive",
                        help="裁剪对象（默认 archive；memory 裁剪会把条目移入归档）")
    p_keep.add_argument("--apply", action="store_true", help="真正执行（默认只预览）")
    p_keep.set_defaults(func=cmd_keep)

    p_dedupe = sub.add_parser("dedupe", help="合并完全重复的条目（保留最新）")
    p_dedupe.add_argument("--target", choices=TARGETS, default="both", help="处理对象（默认 both）")
    p_dedupe.add_argument("--apply", action="store_true", help="真正执行（默认只预览）")
    p_dedupe.set_defaults(func=cmd_dedupe)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
