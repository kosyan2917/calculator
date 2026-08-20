#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artcalc.optimizer import ArtifactBuildOptimizer, OptimizationRequest, OptimizerConfig
from artcalc.solver_catalog import SolverCatalog


def parse_json(value: str) -> dict[str, float]:
    path = Path(value)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    else:
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            data = {}
            compact = value.strip().removeprefix("{").removesuffix("}")
            for item in compact.split(","):
                separator = "=" if "=" in item else ":"
                key, raw_value = item.split(separator, 1)
                data[key.strip().strip("\"'")] = float(raw_value)
    return {str(key): float(item) for key, item in data.items()}


def parse_ids(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize artifact builds directly for a user query.")
    parser.add_argument("--catalog", default="data/solver_catalog.json")
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--targets", required=True, help="Required stat bounds as JSON or a JSON file path")
    parser.add_argument("--armor-ids", default="")
    parser.add_argument("--container-ids", default="")
    parser.add_argument("--quality-tiers", default="")
    parser.add_argument("--exclude-artifact-ids", default="")
    parser.add_argument("--min-quality-percent", type=float)
    parser.add_argument("--min-build-price", type=int, default=0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--time-limit-per-solve", type=float, default=0.5)
    parser.add_argument("--solutions-per-container", type=int, default=6)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--nonlinear-iterations", type=int, default=4)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    optimizer = ArtifactBuildOptimizer(
        SolverCatalog.read(args.catalog),
        OptimizerConfig(
            time_limit_per_solve=args.time_limit_per_solve,
            max_solutions_per_container=args.solutions_per_container,
            num_search_workers=args.workers,
            nonlinear_iterations=args.nonlinear_iterations,
        ),
    )
    result = optimizer.search(
        OptimizationRequest(
            budget=args.budget,
            targets=parse_json(args.targets),
            armor_ids=parse_ids(args.armor_ids),
            container_ids=parse_ids(args.container_ids),
            allowed_quality_tiers=parse_ids(args.quality_tiers),
            excluded_artifact_ids=parse_ids(args.exclude_artifact_ids),
            min_quality_percent=args.min_quality_percent,
            min_build_price=args.min_build_price,
            max_results=args.limit,
        )
    )
    output = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        print(f"Wrote {path}: {result.diagnostics}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
