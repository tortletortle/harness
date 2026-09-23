"""Remove only this run's accidental stress entries from the shared caches.

Default is read-only. --apply makes byte-for-byte backups under the OS temp
folder, checks for concurrent modifications, then atomically replaces each
JSON file. Never clears ordinary user entries.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]


def _ours(name: str, entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if name == "design_cache.json":
        request = entry.get("user_request")
        return isinstance(request, str) and request.startswith("隔离测试 ARENA_STRESS_")
    return "stress_ARENA_STRESS_" in json.dumps(entry, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="back up and remove marked entries")
    args = parser.parse_args()
    backup_dir = (Path(tempfile.gettempdir()) /
                  ("arena-stress-cache-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    for name in ("design_cache.json", "plan_cache.json"):
        path = REPO / "data" / name
        if not path.exists():
            print(name, "absent")
            continue
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{name} is not a cache object; refusing to modify")
        selected = [key for key, value in data.items() if _ours(name, value)]
        print(name, "total", len(data), "marked", len(selected))
        if not args.apply or not selected:
            continue
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False if not backup_dir.exists() else True)
        backup = backup_dir / name
        with backup.open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        remaining = {key: value for key, value in data.items() if key not in set(selected)}
        temporary = path.with_name(path.name + ".stress-" + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as out:
                json.dump(remaining, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            if path.read_bytes() != raw:
                raise RuntimeError(f"{name} changed concurrently; backup kept, no replacement")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        print(name, "remaining", len(remaining), "backup", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
