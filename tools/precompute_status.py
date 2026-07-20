#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print current precompute progress.")
    parser.add_argument("--progress", default="data/precompute_progress.json")
    return parser.parse_args()


def fmt_seconds(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    value = int(round(float(value)))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def estimate_remaining(progress: dict) -> float | None:
    elapsed = float(progress.get("elapsed_seconds") or 0.0)
    completed = int(progress.get("completed_containers") or 0)
    total = int(progress.get("containers_total") or 0)
    if completed <= 0 or total <= 0:
        return None
    avg = elapsed / completed
    return max(0.0, avg * (total - completed))


def main() -> int:
    args = parse_args()
    path = Path(args.progress)
    if not path.exists():
        print(f"No progress file: {path}")
        return 1

    progress = json.loads(path.read_text(encoding="utf-8"))
    status = progress.get("status")
    phase = progress.get("phase")
    container = progress.get("container") or {}
    completed = progress.get("completed_containers")
    total = progress.get("containers_total")
    elapsed = progress.get("elapsed_seconds")
    remaining = estimate_remaining(progress)

    print(f"status: {status}")
    if phase:
        print(f"phase: {phase}")
    print(f"elapsed: {fmt_seconds(elapsed)}")
    if total:
        print(f"containers: {completed or 0}/{total}")
    if container:
        print(f"current_container: {container.get('name')} ({container.get('container_id')})")
    if progress.get("slot"):
        print(f"slot: {progress.get('slot')}/{progress.get('slots_total')}")
    if progress.get("beam_states_total"):
        print(f"beam: {progress.get('beam_state_index')}/{progress.get('beam_states_total')}")
    if progress.get("expanded_states") is not None:
        print(f"expanded_states: {progress.get('expanded_states')}")
    if progress.get("frontier_builds_so_far") is not None:
        print(f"frontier_builds_so_far: {progress.get('frontier_builds_so_far')}")
    if remaining is not None and status not in {"done", "failed"}:
        print(f"rough_remaining: {fmt_seconds(remaining)}")
    if progress.get("counts"):
        print(f"counts: {progress.get('counts')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
