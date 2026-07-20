#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artcalc.profile_query import ProfileQueryConfig, ProfileQueryEngine


def parse_profile(value: str) -> dict[str, float]:
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return json.loads(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query builds by -2..2 preference profiles.")
    parser.add_argument("--precomputed", default="data/precomputed_builds.json")
    parser.add_argument("--armor-stats", default="data/armor_stats.json")
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = ProfileQueryEngine(
        ProfileQueryConfig(
            precomputed_path=args.precomputed,
            armor_stats_path=args.armor_stats,
            max_results=args.limit,
        )
    )
    result = engine.query(args.budget, parse_profile(args.profile), args.limit)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote {args.output}: {result['counts']}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
