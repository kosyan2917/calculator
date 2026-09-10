#!/usr/bin/env python3
"""Build local STALZONE artifact price tables from STALDATA public endpoints."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


QUALITY_ORDER = {
    "common": 0,
    "uncommon": 1,
    "special": 2,
    "rare": 3,
    "exclusive": 4,
    "legendary": 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch artifact prices by rarity from staldata.org.",
    )
    parser.add_argument("--db-root", default="stalzone-database", help="Path to stalzone-database checkout.")
    parser.add_argument("--region", default="RU", help="Market region, for example RU or EU.")
    parser.add_argument("--lang", default="ru", help="Item name language.")
    parser.add_argument(
        "--api-base",
        default="https://staldata.org/api/stalcraft/public",
        help="STALDATA public API base URL.",
    )
    parser.add_argument("--output-json", default="data/artifact_prices.json")
    parser.add_argument("--output-csv", default="data/artifact_prices.csv")
    parser.add_argument("--cache-dir", default="data/staldata_cache/artifact_matrix")
    parser.add_argument("--offline", action="store_true", help="Only read cached STALDATA responses.")
    parser.add_argument("--min-recent-sales", type=int, default=5)
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument("--max-artifacts", type=int, default=0, help="Debug limit; 0 means all artifacts.")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def translated_name(item: dict[str, Any], lang: str) -> str:
    lines = item.get("name", {}).get("lines", {})
    return lines.get(lang) or lines.get("ru") or lines.get("en") or item.get("id", "")


def discover_artifacts(db_root: Path, lang: str) -> list[dict[str, str]]:
    artifact_root = db_root / lang / "items" / "artefact"
    if not artifact_root.exists():
        raise FileNotFoundError(f"Artifact directory not found: {artifact_root}")

    artifacts: list[dict[str, str]] = []
    for path in sorted(artifact_root.rglob("*.json")):
        if "_variants" in path.parts:
            continue
        item = read_json(path)
        item_id = item.get("id")
        if not item_id:
            continue
        artifacts.append(
            {
                "item_id": item_id,
                "name": translated_name(item, lang),
                "category": item.get("category", ""),
                "database_path": str(path.as_posix()),
            }
        )
    return artifacts


def request_json(api_base: str, path: str, params: dict[str, Any], retries: int = 3) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}/{path.lstrip('/')}?{urlencode(params, doseq=True)}"
    curl = shutil.which("curl.exe")
    if curl:
        last_error = ""
        for attempt in range(retries):
            completed = subprocess.run(
                [curl, "--silent", "--show-error", "--location", "--noproxy", "*", "--max-time", "30", url],
                check=False,
                capture_output=True,
            )
            if completed.returncode == 0:
                return json.loads(completed.stdout.decode("utf-8"))
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            stdout = completed.stdout.decode("utf-8", errors="replace").strip()
            last_error = stderr or stdout
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Failed to fetch {url}: curl exited with {completed.returncode}: {last_error}")

    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ArtCalc/0.1"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def local_basis(row: dict[str, Any], min_recent_sales: int) -> tuple[int | None, str, str]:
    fair_price = as_int(row.get("fair_price"))
    best_ask = as_int(row.get("best_ask"))
    fair_source = row.get("fair_price_source")
    fair_status = row.get("fair_price_status")
    windows = row.get("aligned_sales_windows") or {}
    sales_7d = int(windows.get("sales_7d", row.get("sales_7d")) or 0)
    sales_count = int(windows.get("sales_30d", row.get("sales_count")) or 0)

    if fair_price is not None:
        if fair_source == "raw_7d" and sales_7d >= min_recent_sales:
            return fair_price, "recent_7d", "highest"
        if fair_source in {"raw_7d", "raw_30d"} and sales_count >= min_recent_sales:
            return fair_price, "recent_30d", "high"
        if fair_source == "history_3h" and sales_count >= min_recent_sales:
            return fair_price, "older_history", "medium"
        if fair_status == "low_data" or sales_count > 0:
            return fair_price, "low_data_history", "low"
        return fair_price, "fair_price", "medium"

    if best_ask is not None:
        return best_ask, "active_ask_only", "very_low"

    return None, "unavailable", "none"


def normalize_segment(artifact: dict[str, str], row: dict[str, Any], min_recent_sales: int) -> dict[str, Any]:
    price, basis, local_confidence = local_basis(row, min_recent_sales)
    windows = row.get("aligned_sales_windows") or {}
    return {
        "item_id": artifact["item_id"],
        "name": artifact["name"],
        "category": artifact["category"],
        "quality_tier": row.get("quality_tier"),
        "quality_raw": row.get("quality_raw"),
        "upgrade_level": row.get("upgrade_level"),
        "price": price,
        "price_basis": basis,
        "local_confidence": local_confidence,
        "staldata_fair_price": as_int(row.get("fair_price")),
        "staldata_fair_price_status": row.get("fair_price_status"),
        "staldata_fair_price_source": row.get("fair_price_source"),
        "best_ask": as_int(row.get("best_ask")),
        "market_ask_p20": as_int(row.get("market_ask_p20")),
        "active_lots": int(row.get("active_lots") or 0),
        "sales_count": int(row.get("sales_count") or 0),
        "sales_7d": int(windows.get("sales_7d", row.get("sales_7d")) or 0),
        "sales_30d": int(windows.get("sales_30d") or 0),
        "sales_window_end": windows.get("window_end"),
        "sample_count": int(row.get("sample_count") or 0),
        "liquidity_score": as_float(row.get("liquidity_score")),
        "risk_score": as_float(row.get("risk_score")),
        "risk_level": row.get("risk_level"),
        "confidence_score": as_float(row.get("confidence_score")),
        "confidence_level": row.get("confidence_level"),
        "segment_key": row.get("segment_key"),
    }


def sort_key(segment: dict[str, Any]) -> tuple[str, int, int]:
    quality = str(segment.get("quality_tier") or "")
    upgrade = int(segment.get("upgrade_level") or 0)
    return (segment["item_id"], QUALITY_ORDER.get(quality, 999), upgrade)


def cache_path(cache_dir: Path, region: str, item_id: str) -> Path:
    return cache_dir / region.upper() / f"{item_id}.json"


def load_matrix(
    api_base: str,
    cache_dir: Path,
    region: str,
    item_id: str,
    offline: bool,
) -> dict[str, Any]:
    path = cache_path(cache_dir, region, item_id)
    if path.exists():
        return read_json(path)
    if offline:
        raise FileNotFoundError(f"Cached matrix not found: {path}")
    return request_json(api_base, "/market/artifact-matrix", {"region": region.upper(), "item_id": item_id})


def write_csv(path: Path, segments: list[dict[str, Any]]) -> None:
    fields = [
        "item_id",
        "name",
        "category",
        "quality_tier",
        "quality_raw",
        "upgrade_level",
        "price",
        "price_basis",
        "local_confidence",
        "staldata_fair_price",
        "staldata_fair_price_status",
        "staldata_fair_price_source",
        "best_ask",
        "market_ask_p20",
        "active_lots",
        "sales_count",
        "sales_7d",
        "sample_count",
        "liquidity_score",
        "risk_score",
        "risk_level",
        "confidence_score",
        "confidence_level",
        "segment_key",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(segments)


def main() -> int:
    args = parse_args()
    db_root = Path(args.db_root)
    artifacts = discover_artifacts(db_root, args.lang)
    if args.max_artifacts:
        artifacts = artifacts[: args.max_artifacts]

    all_segments: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for index, artifact in enumerate(artifacts, start=1):
        print(f"[{index}/{len(artifacts)}] {artifact['item_id']} {artifact['name']}", file=sys.stderr)
        try:
            data = load_matrix(
                args.api_base,
                Path(args.cache_dir),
                args.region,
                artifact["item_id"],
                args.offline,
            )
            for row in data.get("matrix", []):
                segment = normalize_segment(artifact, row, args.min_recent_sales)
                segment["observed_at"] = data.get("generated_at")
                segment["history_from"] = data.get("data_from")
                segment["history_to"] = data.get("data_to")
                all_segments.append(segment)
        except Exception as error:  # Keep partial market output useful.
            failures.append({"item_id": artifact["item_id"], "name": artifact["name"], "error": str(error)})
        if args.request_delay > 0:
            time.sleep(args.request_delay)

    all_segments.sort(key=sort_key)
    base_segments = [segment for segment in all_segments if segment.get("upgrade_level") == 0]
    base_prices: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        prices_by_rarity = {
            segment["quality_tier"]: segment
            for segment in base_segments
            if segment["item_id"] == artifact["item_id"] and segment.get("quality_tier")
        }
        if prices_by_rarity:
            base_prices[artifact["item_id"]] = {
                "item_id": artifact["item_id"],
                "name": artifact["name"],
                "category": artifact["category"],
                "prices_by_rarity": prices_by_rarity,
            }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region": args.region.upper(),
        "language": args.lang,
        "source": {
            "site": "https://staldata.org/",
            "api_base": args.api_base,
            "endpoint": "/market/artifact-matrix",
        },
        "methodology": {
            "price_field": "price",
            "price_selection": [
                "Prefer STALDATA fair_price when available.",
                "raw_7d with enough sales is labeled recent_7d and gets the strongest local confidence.",
                "raw_30d is used when a 7-day segment is thin but the 30-day sample is usable.",
                "history_3h is kept for low-liquidity segments and marked as older_history or low_data_history.",
                "If there is no fair_price but an active buyout exists, price falls back to best_ask with very_low confidence.",
            ],
            "min_recent_sales": args.min_recent_sales,
        },
        "counts": {
            "artifacts_in_database": len(artifacts),
            "segments": len(all_segments),
            "base_segments_upgrade_0": len(base_segments),
            "artifacts_with_prices": len(base_prices),
            "failures": len(failures),
        },
        "base_prices": base_prices,
        "segments": all_segments,
        "failures": failures,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(Path(args.output_csv), all_segments)

    print(
        f"Wrote {output_json} and {args.output_csv}: "
        f"{len(all_segments)} segments, {len(base_segments)} +0 segments, {len(failures)} failures.",
        file=sys.stderr,
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
