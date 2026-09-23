"""Remove only temporary roots referenced by this experiment's JSON evidence.

Dry-run by default. Report artifacts, the cumulative budget ledger, and normal
project data remain untouched. A report is written after its child exits; never
run --apply while a test command is still running.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile


def referenced_roots():
    reports = Path(__file__).resolve().parents[1] / "reports" / "stress-20260923"
    temp = Path(tempfile.gettempdir()).resolve()
    roots = set()
    for report in reports.glob("*.json"):
        root = json.loads(report.read_text(encoding="utf-8")).get("temp_root")
        if root is None:
            continue
        if not isinstance(root, str):
            raise RuntimeError("invalid stress evidence root")
        path = Path(root)
        if (path.is_symlink() or path.parent.resolve() != temp
                or re.fullmatch(r"arena-eas-stress-[a-z0-9_]{8}", path.name) is None):
            raise RuntimeError("refusing an unrelated or redirected temp directory")
        roots.add(path)
    return sorted(roots)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete only validated referenced temp roots")
    args = parser.parse_args()
    for root in referenced_roots():
        if not root.exists():
            continue
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError("stress root changed before cleanup")
        print("remove" if args.apply else "would_remove", root.name)
        if args.apply:
            shutil.rmtree(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
