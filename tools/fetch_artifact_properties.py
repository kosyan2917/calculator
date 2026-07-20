#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


STAT_PREFIX = "stalker.artefact_properties.factor."
DEFAULT_PAYLOAD_HASH = "cc9fb791-8f51-4538-a9eb-a168bde2a748"
MANUAL_LABEL_TO_SUFFIX = {
    "Химзащита": "chemical_burn_dmg_factor",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch local Multitool artifact property snapshots.")
    parser.add_argument("--base-url", default="https://multitoolsx.live")
    parser.add_argument("--db-root", default="stalzone-database")
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--output-full", default="data/artifact_multitool_properties.json")
    parser.add_argument("--output-additional", default="data/artifact_additional_properties.json")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--max-artifacts", type=int, default=0, help="Debug limit; 0 means all artifacts.")
    return parser.parse_args()


def request_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "ArtCalc/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def request_json(url: str) -> Any:
    return json.loads(request_text(url))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def text_value(node: dict[str, Any] | None, lang: str) -> str:
    if not node:
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    lines = node.get("lines") or {}
    return str(lines.get(lang) or lines.get("ru") or lines.get("en") or "")


def build_label_to_suffix(db_root: Path, lang: str) -> dict[str, str]:
    result: dict[str, str] = dict(MANUAL_LABEL_TO_SUFFIX)
    artifact_root = db_root / lang / "items" / "artefact"
    for path in sorted(artifact_root.rglob("*.json")):
        if "_variants" in path.parts:
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for block in item.get("infoBlocks") or []:
            for element in block.get("elements") or []:
                name_node = element.get("name") or {}
                stat_id = name_node.get("key") or (element.get("key") or {}).get("key")
                if not isinstance(stat_id, str) or not stat_id.startswith(STAT_PREFIX):
                    continue
                label = text_value(name_node, lang)
                if label:
                    result.setdefault(label, stat_id.removeprefix(STAT_PREFIX))
    return result


def fetch_artifact_list(base_url: str, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urlencode(
            {
                "category": "artifact",
                "limit": limit,
                "page": page,
                "sortBy": "name",
                "sortOrder": "asc",
            }
        )
        data = request_json(f"{base_url}/api/wiki/items?{query}")
        items.extend(data.get("items") or [])
        total = int(data.get("total") or len(items))
        current_limit = int(data.get("limit") or limit)
        if len(items) >= total or page * current_limit >= total:
            break
        page += 1
    return items


def table_value(table: list[Any], ref: Any) -> Any:
    if isinstance(ref, int) and 0 <= ref < len(table):
        return table[ref]
    return ref


def object_field(table: list[Any], node: dict[str, Any], key: str) -> Any:
    if key not in node:
        return None
    return table_value(table, node[key])


def payload_url(base_url: str, slug: str) -> str:
    return f"{base_url}/wiki/item/artifact/{slug}/_payload.json?{DEFAULT_PAYLOAD_HASH}"


def discover_payload_url(base_url: str, slug: str) -> str:
    html = request_text(f"{base_url}/wiki/item/artifact/{slug}")
    match = re.search(r'data-src="([^"]+_payload\.json[^"]*)"', html)
    if not match:
        raise RuntimeError(f"payload link not found for {slug}")
    src = match.group(1)
    if src.startswith("http"):
        return src
    return f"{base_url}{src}"


def fetch_item_payload(base_url: str, slug: str) -> list[Any]:
    url = payload_url(base_url, slug)
    try:
        return request_json(url)
    except (HTTPError, URLError, json.JSONDecodeError):
        return request_json(discover_payload_url(base_url, slug))


def find_payload_item(table: list[Any]) -> dict[str, Any]:
    for node in table:
        if not isinstance(node, dict):
            continue
        for key, ref in node.items():
            if isinstance(key, str) and key.startswith("wiki-item-by-slug-artifact-"):
                item = table_value(table, ref)
                if isinstance(item, dict):
                    return item
    raise RuntimeError("item node not found in payload")


def parse_range(table: list[Any], node_ref: Any) -> dict[str, Any]:
    node = table_value(table, node_ref)
    if not isinstance(node, dict):
        return {}
    key = object_field(table, node, "key")
    label = object_field(table, node, "label")
    full_key = object_field(table, node, "fullKey")
    return {
        "key": key,
        "stat_suffix": str(key) if key else None,
        "label": label,
        "full_key": full_key,
        "min": object_field(table, node, "min"),
        "max": object_field(table, node, "max"),
        "formatted": object_field(table, node, "formatted"),
    }


def parse_additional_property(
    table: list[Any],
    node_ref: Any,
    label_to_suffix: dict[str, str],
) -> tuple[dict[str, Any], str | None]:
    node = table_value(table, node_ref)
    if not isinstance(node, dict):
        return {}, None
    label = object_field(table, node, "label")
    full_key = object_field(table, node, "fullKey")
    key = object_field(table, node, "key")
    stat_suffix = None
    if isinstance(key, str):
        stat_suffix = key
    elif isinstance(full_key, str) and full_key.startswith(STAT_PREFIX):
        stat_suffix = full_key.removeprefix(STAT_PREFIX)
    elif isinstance(label, str):
        stat_suffix = label_to_suffix.get(label)
    result = {
        "stat_suffix": stat_suffix,
        "label": label,
        "min": object_field(table, node, "min"),
        "max": object_field(table, node, "max"),
        "raw": object_field(table, node, "raw"),
        "is_percent": object_field(table, node, "isPercent"),
        "sign": object_field(table, node, "sign"),
    }
    unknown_label = str(label) if not stat_suffix and label else None
    return result, unknown_label


def parse_payload_properties(
    table: list[Any],
    label_to_suffix: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    item = find_payload_item(table)
    props = table_value(table, item.get("properties"))
    if not isinstance(props, dict):
        props = {}

    ranges = []
    ranges_ref = props.get("__ranges")
    if ranges_ref is not None:
        ranges_node = table_value(table, ranges_ref)
        if isinstance(ranges_node, list):
            ranges = [parsed for parsed in (parse_range(table, ref) for ref in ranges_node) if parsed]

    variants = []
    variants_ref = props.get("__variants")
    if variants_ref is not None:
        variants_node = table_value(table, variants_ref)
        if isinstance(variants_node, list):
            for variant_ref in variants_node:
                variant = table_value(table, variant_ref)
                if not isinstance(variant, dict):
                    continue
                variant_ranges = table_value(table, variant.get("ranges"))
                variants.append(
                    {
                        "quality": object_field(table, variant, "quality"),
                        "ranges": [
                            parsed
                            for parsed in (parse_range(table, ref) for ref in variant_ranges or [])
                            if parsed
                        ],
                    }
                )

    additional = []
    unknown_labels: list[str] = []
    additional_ref = props.get("__additionalProperties")
    if additional_ref is not None:
        additional_node = table_value(table, additional_ref)
        if isinstance(additional_node, list):
            for ref in additional_node:
                parsed, unknown_label = parse_additional_property(table, ref, label_to_suffix)
                if parsed:
                    additional.append(parsed)
                if unknown_label:
                    unknown_labels.append(unknown_label)

    return {"ranges": ranges, "variants": variants}, additional, unknown_labels


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    label_to_suffix = build_label_to_suffix(Path(args.db_root), args.lang)
    artifacts = fetch_artifact_list(base_url, args.limit)
    if args.max_artifacts:
        artifacts = artifacts[: args.max_artifacts]

    full_items: dict[str, Any] = {}
    additional_items: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    unknown_labels: dict[str, list[str]] = {}

    for index, artifact in enumerate(artifacts, start=1):
        exbo_id = str(artifact.get("exboId") or "")
        slug = str(artifact.get("slug") or "")
        name = str(artifact.get("name") or "")
        if not exbo_id or not slug:
            continue
        source_url = f"{base_url}/wiki/item/artifact/{slug}"
        print(f"[{index}/{len(artifacts)}] {exbo_id} {name}", file=sys.stderr)
        try:
            table = fetch_item_payload(base_url, slug)
            properties, additional, item_unknown_labels = parse_payload_properties(table, label_to_suffix)
            sources[exbo_id] = source_url
            full_items[exbo_id] = {
                "item_id": exbo_id,
                "name": name,
                "name_en": artifact.get("nameEn"),
                "slug": slug,
                "category_path": artifact.get("categoryPath"),
                "subcategory": artifact.get("subcategory"),
                "source_url": source_url,
                "ranges": properties["ranges"],
                "variants": properties["variants"],
                "additional_properties": additional,
            }
            if additional:
                additional_items[exbo_id] = [
                    {
                        key: value
                        for key, value in property_item.items()
                        if key in {"stat_suffix", "label", "min", "max", "raw", "is_percent", "sign"}
                    }
                    for property_item in additional
                ]
            if item_unknown_labels:
                unknown_labels[exbo_id] = sorted(set(item_unknown_labels))
        except Exception as error:  # noqa: BLE001 - export should keep going and report failures.
            failures.append({"item_id": exbo_id, "name": name, "slug": slug, "error": str(error)})
        if args.delay > 0:
            time.sleep(args.delay)

    generated_at = datetime.now(timezone.utc).isoformat()
    full_payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source": base_url,
        "counts": {
            "artifacts": len(artifacts),
            "items_exported": len(full_items),
            "items_with_additional_properties": len(additional_items),
            "failures": len(failures),
            "unknown_label_items": len(unknown_labels),
        },
        "items": full_items,
        "failures": failures,
        "unknown_labels": unknown_labels,
    }
    additional_payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_notes": [
            "Additional artifact roll slots are not exposed in stalzone-database artifact item JSON files.",
            "Ranges are stored before artifact level scaling; runtime applies (50 + upgrade_level) / 50.",
            "All listed additional properties are applied by default because build calculations assume +15 artifacts.",
            "Fetched from Multitool item pages.",
        ],
        "sources": sources,
        "counts": {
            "artifacts": len(artifacts),
            "items_with_additional_properties": len(additional_items),
            "failures": len(failures),
            "unknown_label_items": len(unknown_labels),
        },
        "items": additional_items,
        "failures": failures,
        "unknown_labels": unknown_labels,
    }

    write_json(Path(args.output_full), full_payload)
    write_json(Path(args.output_additional), additional_payload)
    print(
        "Wrote "
        f"{args.output_full} ({len(full_items)} items) and "
        f"{args.output_additional} ({len(additional_items)} items with additional properties)."
    )
    if failures:
        print(f"Failures: {len(failures)}", file=sys.stderr)
    if unknown_labels:
        print(f"Unknown labels: {unknown_labels}", file=sys.stderr)
    return 1 if failures or unknown_labels else 0


if __name__ == "__main__":
    raise SystemExit(main())
