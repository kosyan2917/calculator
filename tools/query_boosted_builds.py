#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artcalc.boosts import BoostedBuildQueryEngine, BoostedQueryConfig


def parse_json_arg(value: str | None) -> dict:
    if not value:
        return {}
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def split_ids(value: str) -> set[str] | None:
    ids = {item.strip() for item in value.split(",") if item.strip()}
    return ids or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query builds that pass targets only with consumable boosts.")
    parser.add_argument("--precomputed", default="data/precomputed_builds.json")
    parser.add_argument("--armor-stats", default="data/armor_stats.json")
    parser.add_argument("--boosts", default="data/boosts.json")
    parser.add_argument("--budget", type=int)
    parser.add_argument("--targets", default="{}")
    parser.add_argument("--weights", default="{}")
    parser.add_argument("--armor-ids", default="")
    parser.add_argument("--container-ids", default="")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--base-candidate-limit", type=int, default=800)
    parser.add_argument("--boosts-per-type", type=int, default=4)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = BoostedBuildQueryEngine(
        BoostedQueryConfig(
            precomputed_path=args.precomputed,
            armor_stats_path=args.armor_stats,
            boosts_path=args.boosts,
            max_results=args.limit,
            base_candidate_limit=args.base_candidate_limit,
            boosts_per_type=args.boosts_per_type,
        )
    )
    result = engine.query_only_with_boosts(
        budget=args.budget,
        targets=parse_json_arg(args.targets),
        weights=parse_json_arg(args.weights),
        armor_ids=split_ids(args.armor_ids),
        container_ids=split_ids(args.container_ids),
        max_results=args.limit,
        base_candidate_limit=args.base_candidate_limit,
        boosts_per_type=args.boosts_per_type,
    )
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
