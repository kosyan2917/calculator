#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artcalc.solver_catalog import ArtifactCatalogCompiler, CatalogCompilerConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile the query-time optimizer catalog.")
    parser.add_argument("--db-root", default="stalzone-database")
    parser.add_argument("--artifact-prices", default="data/artifact_prices.json")
    parser.add_argument("--artifact-additional-properties", default="data/artifact_additional_properties.json")
    parser.add_argument("--armor-stats", default="data/armor_stats.json")
    parser.add_argument("--output", default="data/solver_catalog.json")
    parser.add_argument("--min-quality-percent", type=float, default=95.0)
    parser.add_argument("--max-artifact-price", type=int, default=150_000_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = ArtifactCatalogCompiler(
        CatalogCompilerConfig(
            db_root=args.db_root,
            artifact_prices_path=args.artifact_prices,
            artifact_additional_properties_path=args.artifact_additional_properties,
            armor_stats_path=args.armor_stats,
            min_quality_percent=args.min_quality_percent,
            max_artifact_price=args.max_artifact_price,
        )
    ).compile()
    catalog.write(args.output)
    print(
        f"Wrote {args.output}: {len(catalog.artifact_groups)} artifact groups, "
        f"{len(catalog.containers)} containers, {len(catalog.armors)} armors."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
