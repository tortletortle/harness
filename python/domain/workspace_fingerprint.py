"""Bounded, content-free fingerprints for execution-attempt diagnostics.

A fingerprint is an observation of a workspace at one instant, not a restorable
snapshot and not proof that two executions were causally controlled.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Optional

_MAX_PATH_LIST_BYTES = 1024 * 1024
_MAX_CHANGED_FILES = 256
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_DEPENDENCY_FILES = (
    "pyproject.toml", "requirements.txt", "python/requirements.txt",
    "requirements-dev.txt", "uv.lock", "poetry.lock", "Pipfile.lock",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "frontend/package.json", "frontend/package-lock.json",
    "frontend/pnpm-lock.yaml", "frontend/yarn.lock",
    "Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
)


def _digest_file(root: Path, relative: str, budget: list[int], *, allow_missing: bool = False) -> Optional[str]:
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        return None
    target = root.joinpath(*pure.parts)
    try:
        try:
            info = target.lstat()
        except FileNotFoundError:
            return hashlib.sha256(b\"deleted\").hexdigest() if allow_missing else None
        if target.is_symlink():
            payload = os.readlink(target).encode("utf-8", "surrogateescape")
        elif target.is_file():
            if info.st_size > _MAX_FILE_BYTES or budget[0] + info.st_size > _MAX_TOTAL_BYTES:
                return None
            hasher = hashlib.sha256()
            with target.open("rb") as stream:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    budget[0] += len(chunk)
                    if budget[0] > _MAX_TOTAL_BYTES:
                        return None
                    hasher.update(chunk)
            after = target.lstat()
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                return None
            return hasher.hexdigest()
        else:
            return None
        return hashlib.sha256(payload).hexdigest()
    except (OSError, ValueError):
        return None


def _git(root: Path, *args: str) -> Optional[bytes]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > _MAX_PATH_LIST_BYTES:
        return None
    return result.stdout


def capture_workspace_fingerprint(workspace_root=None) -> dict:
    """Return bounded hashes and completeness flags; never return file contents or paths."""
    try:
        from tools.workspace import resolve_workspace_root
        root = Path(workspace_root).resolve() if workspace_root is not None else resolve_workspace_root()
        if not root.is_dir():
            return {"version": 1, "complete": False, "error_code": "workspace_unavailable"}

        head_raw = _git(root, "rev-parse", "--verify", "HEAD")
        if head_raw is None:
            return {"version": 1, "complete": False, "error_code": "git_head_unavailable"}
        source_revision = head_raw.decode("ascii", "ignore").strip()
        if not source_revision or len(source_revision) > 64:
            return {"version": 1, "complete": False, "error_code": "git_head_invalid"}

        tracked_raw = _git(root, "diff", "--name-only", "-z", "HEAD")
        untracked_raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
        if tracked_raw is None or untracked_raw is None:
            return {"version": 1, "complete": False, "error_code": "git_status_unavailable"}

        tracked = [os.fsdecode(item) for item in tracked_raw.split(b"\0") if item]
        untracked = [os.fsdecode(item) for item in untracked_raw.split(b"\0") if item]
        if len(tracked) + len(untracked) > _MAX_CHANGED_FILES:
            return {"version": 1, "complete": False, "error_code": "changed_file_limit"}

        budget = [0]
        changed_entries = []
        for label, paths in (("tracked", tracked), ("untracked", untracked)):
            for relative in paths:
                digest = _digest_file(root, relative, budget, allow_missing=(label == \"tracked\"))
                if digest is None:
                    return {"version": 1, "complete": False, "error_code": "changed_file_unreadable"}
                path_hash = hashlib.sha256(relative.encode("utf-8", "surrogateescape")).hexdigest()
                changed_entries.append((label, path_hash, digest))

        dependency_entries = []
        for relative in _DEPENDENCY_FILES:
            if not (root / relative).exists():
                continue
            digest = _digest_file(root, relative, budget)
            if digest is None:
                return {"version": 1, "complete": False, "error_code": "dependency_manifest_unreadable"}
            dependency_entries.append((relative, digest))

        runtime = {
            "implementation": sys.implementation.name,
            "python": list(sys.version_info[:3]),
            "os": os.name,
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }
        runtime_sha256 = hashlib.sha256(
            json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        dependency_sha256 = hashlib.sha256(
            json.dumps(sorted(dependency_entries), separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        workspace_sha256 = hashlib.sha256(json.dumps({
            "source_revision": source_revision,
            "changed": sorted(changed_entries),
            "dependency_sha256": dependency_sha256,
            "runtime_sha256": runtime_sha256,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        return {
            "version": 1,
            "complete": True,
            "source_revision": source_revision,
            "workspace_sha256": workspace_sha256,
            "dependency_sha256": dependency_sha256,
            "runtime_sha256": runtime_sha256,
            "tracked_changed_files": len(tracked),
            "untracked_files": len(untracked),
            "hashed_bytes": budget[0],
            "error_code": None,
        }
    except Exception:
        # Diagnostics must never prevent the task itself from running.
        return {"version": 1, "complete": False, "error_code": "fingerprint_internal_error"}
