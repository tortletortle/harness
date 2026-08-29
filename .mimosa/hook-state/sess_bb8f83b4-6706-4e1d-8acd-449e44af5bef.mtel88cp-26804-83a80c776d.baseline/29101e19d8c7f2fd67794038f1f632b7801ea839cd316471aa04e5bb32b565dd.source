"""统一的本地开发与测试 harness 入口。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
REPORTS = ROOT / ".harness" / "reports"


def run(command: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    executable = shutil.which(command[0])
    if executable:
        command = [executable, *command[1:]]
    print(f"\n$ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def check() -> None:
    print(f"root: {ROOT}")
    print(f"python: {sys.executable}")
    print(f"python version: {sys.version.split()[0]}")
    for executable in ("node", "npm", "cargo"):
        path = shutil.which(executable)
        print(f"{executable}: {path or 'not found'}")
    required = [ROOT / "python" / "api" / "server.py", FRONTEND / "package.json"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("missing required project files: " + ", ".join(missing))
    print("configuration: OK (offline mode; real LLM is not required)")


def unit() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("EAS_HARNESS_MODE", "offline")
    run([sys.executable, "-m", "pytest", "tests", "--junitxml", str(REPORTS / "python-unit.xml")], env=env)
    run(["npm", "run", "test", "--", "--reporter=verbose"], cwd=FRONTEND, env=env)
    if shutil.which("cargo"):
        run(["cargo", "test"], env=env)


def build() -> None:
    run([sys.executable, "-m", "compileall", "-q", "python"])
    run(["npm", "run", "build"], cwd=FRONTEND)
    if shutil.which("cargo"):
        run(["cargo", "build"])


def main() -> None:
    parser = argparse.ArgumentParser(description="EAS 2.0 local development harness")
    parser.add_argument("command", choices=("check", "unit", "build", "all"))
    args = parser.parse_args()
    if args.command == "check":
        check()
    elif args.command == "unit":
        unit()
    elif args.command == "build":
        build()
    else:
        check()
        unit()
        build()


if __name__ == "__main__":
    main()
