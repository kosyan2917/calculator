#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artcalc.precompute import BuildPrecomputer, PrecomputeConfig
from artcalc.stat_model import MechanicsConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precompute Pareto artifact builds by container.")
    parser.add_argument("--db-root", default="stalzone-database")
    parser.add_argument("--artifact-prices", default="data/artifact_prices.json")
    parser.add_argument("--artifact-additional-properties", default="data/artifact_additional_properties.json")
    parser.add_argument("--output", default="data/precomputed_builds.json")
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--beam-size", type=int, default=800)
    parser.add_argument("--frontier-limit", type=int, default=6000)
    parser.add_argument("--max-artifact-candidates", type=int, default=1500)
    parser.add_argument("--artifact-upgrade-level", type=int, default=15)
    parser.add_argument("--artifact-price-upgrade-level", type=int, default=0)
    parser.add_argument("--quality-policy", choices=["min", "mid", "p75", "max"], default="mid")
    parser.add_argument("--quality-strategy", choices=["single", "grid", "adaptive_grid"], default="adaptive_grid")
    parser.add_argument("--quality-step", type=float, default=2.5)
    parser.add_argument("--min-quality-percent", type=float, default=95.0)
    parser.add_argument("--min-build-price", type=int, default=2_500_000)
    parser.add_argument("--max-build-price", type=int)
    parser.add_argument("--price-bucket-start", type=int, default=2_500_000)
    parser.add_argument("--price-bucket-step", type=int, default=2_500_000)
    parser.add_argument("--price-bucket-beam-size", type=int, default=20)
    parser.add_argument("--max-beam-states", type=int, default=0)
    parser.add_argument(
        "--exclude-artifact-ids",
        default="9n7z",
        help="Comma-separated artifact ids to exclude from precompute. Defaults to Rubik.",
    )
    parser.add_argument("--no-duplicate-artifacts", action="store_true")
    parser.add_argument("--progress", default="data/precompute_progress.json")
    parser.add_argument("--progress-interval-seconds", type=float, default=5.0)
    return parser.parse_args()


def parse_csv_ids(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main() -> int:
    args = parse_args()
    mechanics = MechanicsConfig(quality_policy=args.quality_policy)
    config = PrecomputeConfig(
        db_root=args.db_root,
        artifact_prices_path=args.artifact_prices,
        artifact_additional_properties_path=args.artifact_additional_properties,
        output_path=args.output,
        lang=args.lang,
        artifact_upgrade_level=args.artifact_upgrade_level,
        artifact_price_upgrade_level=args.artifact_price_upgrade_level,
        quality_strategy=args.quality_strategy,
        quality_step=args.quality_step,
        min_quality_percent=args.min_quality_percent,
        min_build_price=args.min_build_price,
        max_build_price=args.max_build_price,
        price_bucket_start=args.price_bucket_start,
        price_bucket_step=args.price_bucket_step,
        price_bucket_beam_size=args.price_bucket_beam_size,
        max_beam_states=args.max_beam_states,
        excluded_artifact_ids=parse_csv_ids(args.exclude_artifact_ids),
        beam_size=args.beam_size,
        frontier_limit_per_container=args.frontier_limit,
        max_artifact_candidates=args.max_artifact_candidates,
        allow_duplicate_artifacts=not args.no_duplicate_artifacts,
        progress_path=args.progress,
        progress_interval_seconds=args.progress_interval_seconds,
        mechanics=mechanics,
    )
    payload = BuildPrecomputer(config).run()
    print(
        "Wrote "
        f"{args.output}: {payload['counts']['frontier_builds']} frontier builds, "
        f"{payload['counts']['containers']} containers, "
        f"{payload['counts']['artifact_candidates']} artifact candidates, "
        f"{payload['counts']['artifact_candidates_excluded']} excluded candidates, "
        f"{payload['counts']['artifact_candidates_excluded_by_price_cap']} price-capped candidates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
