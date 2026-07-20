#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    stdout_path = root / "data" / "precompute_stdout.log"
    stderr_path = root / "data" / "precompute_stderr.log"
    runner_path = root / "data" / "precompute_runner.json"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(root / "tools" / "precompute_builds.py"),
        "--progress",
        str(root / "data" / "precompute_progress.json"),
        "--progress-interval-seconds",
        "5",
    ]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )

    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": process.pid,
        "command": command,
        "stdout_path": str(stdout_path.relative_to(root)),
        "stderr_path": str(stderr_path.relative_to(root)),
        "progress_path": str((root / "data" / "precompute_progress.json").relative_to(root)),
    }
    runner_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"started pid={process.pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
