from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://stalzone.wiki"
USER_AGENT = "ArtCalc combat stats collector/0.1"


STAT_IDS = {
    "registered_at": "reg-tim",
    "playtime_ms": "pla-tim",
    "kills": "kil",
    "deaths": "dea",
    "shots_fired": "sho-fir",
    "shots_hit": "sho-hit",
    "hit_head": "sho-hea",
    "hit_body": "sho-bod",
    "hit_limbs": "sho-lim",
    "deaths_battlefield": "deaths-bf",
    "kills_battlefield": "kills-bf",
    "death_bul_dea": "bul-dea",
    "death_exp_dea": "exp-dea",
    "death_fal_dea": "fal-dea",
    "death_rad_dea": "rad-dea",
    "death_col_dea": "col-dea",
    "death_ble_to_dea": "ble-to-dea",
    "death_ano_dea": "ano-dea",
    "death_lig_ano_dea": "lig-ano-dea",
    "death_car_ano_dea": "car-ano-dea",
    "death_ele_ano_dea": "ele-ano-dea",
    "death_tra_ano_dea": "tra-ano-dea",
    "death_kis_ano_dea": "kis-ano-dea",
    "death_ste_ano_dea": "ste-ano-dea",
    "death_cir_ano_dea": "cir-ano-dea",
    "death_fun_ano_dea": "fun-ano-dea",
    "suicides": "suicides",
}

CSV_FIELDS = [
    "source",
    "region",
    "nickname",
    "username",
    "uuid",
    "alliance",
    "profile_url",
    "fetched_at",
    "last_login",
    "candidate_last_searched_at",
    "candidate_search_count",
    "registered_at",
    "playtime_ms",
    "kills",
    "deaths",
    "kd_raw",
    "shots_fired",
    "shots_hit",
    "accuracy_pct",
    "hit_head",
    "hit_body",
    "hit_limbs",
    "headshot_hit_pct",
    "body_hit_pct",
    "limb_hit_pct",
    "kills_battlefield",
    "deaths_battlefield",
    "death_bul_dea",
    "death_exp_dea",
    "death_fal_dea",
    "death_rad_dea",
    "death_col_dea",
    "death_ble_to_dea",
    "death_ano_dea",
    "death_lig_ano_dea",
    "death_car_ano_dea",
    "death_ele_ano_dea",
    "death_tra_ano_dea",
    "death_kis_ano_dea",
    "death_ste_ano_dea",
    "death_cir_ano_dea",
    "death_fun_ano_dea",
    "suicides",
    "notes",
]

CYRILLIC_CHARS = "абвгдежзийклмнопрстуфхцчшщэюя"
LATIN_CHARS = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"
COMMON_SECOND_CHARS = "аеёиоуыэюяирнстлкмв"
COMMON_LATIN_SECOND_CHARS = "aeiournstlm"


@dataclass(frozen=True)
class Candidate:
    nickname: str
    region: str
    alliance: str | None = None
    search_count: int | None = None
    last_searched_at: str | None = None
    candidate_source: str = "unknown"


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def fetch_json(url: str) -> Any:
    return json.loads(fetch_text(url))


def normalize_region(region: str | None) -> str:
    return (region or "ru").lower()


def candidate_key(candidate: Candidate) -> tuple[str, str]:
    return (normalize_region(candidate.region), candidate.nickname.casefold())


def fetch_candidates(periods: list[str], include_recent: bool, queries: list[str]) -> list[Candidate]:
    candidates: dict[tuple[str, str], Candidate] = {}

    def add_many(rows: list[dict[str, Any]], source: str) -> None:
        for row in rows:
            nickname = row.get("nickname")
            if not nickname:
                continue
            candidate = Candidate(
                nickname=nickname,
                region=normalize_region(row.get("region")),
                alliance=row.get("alliance"),
                search_count=row.get("search_count"),
                last_searched_at=row.get("last_searched_at"),
                candidate_source=source,
            )
            candidates.setdefault(candidate_key(candidate), candidate)

    if include_recent:
        add_many(fetch_json(f"{BASE_URL}/api/characters/recent/"), "stalzone_wiki_recent")

    for period in periods:
        params = urlencode({"period": period})
        add_many(fetch_json(f"{BASE_URL}/api/characters/popular/?{params}"), f"stalzone_wiki_popular_{period}")

    for query in queries:
        params = urlencode({"query": query})
        add_many(fetch_json(f"{BASE_URL}/api/characters/suggestions/?{params}"), f"stalzone_wiki_suggestion_{query}")

    return list(candidates.values())


def discovery_queries(mode: str) -> list[str]:
    if mode == "none":
        return []

    queries: list[str] = []
    queries.extend(CYRILLIC_CHARS)
    queries.extend(LATIN_CHARS)
    queries.extend(DIGITS)

    if mode in {"balanced", "wide"}:
        for first in CYRILLIC_CHARS:
            for second in COMMON_SECOND_CHARS:
                queries.append(first + second)
        for first in LATIN_CHARS:
            for second in COMMON_LATIN_SECOND_CHARS:
                queries.append(first + second)

    if mode == "wide":
        for first in CYRILLIC_CHARS:
            for second in CYRILLIC_CHARS:
                queries.append(first + second)
        for first in LATIN_CHARS:
            for second in LATIN_CHARS:
                queries.append(first + second)

    return list(dict.fromkeys(queries))


def profile_url(candidate: Candidate) -> str:
    region = normalize_region(candidate.region)
    nickname = quote(candidate.nickname, safe="")
    return f"{BASE_URL}/characters/{region}/{nickname}"


def extract_profile(html: str) -> dict[str, Any]:
    marker = '"profile":'
    decoder = json.JSONDecoder()

    # Next.js embeds the profile object inside escaped RSC string chunks.
    pattern = re.compile(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)</script>')
    for match in pattern.finditer(html):
        try:
            text = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            continue
        start = text.find(marker)
        if start < 0:
            continue
        profile, _ = decoder.raw_decode(text[start + len(marker) :])
        if isinstance(profile, dict):
            return profile

    # Fallback for pages where the payload shape changes but the object remains
    # directly recoverable after unescaping structural quotes.
    text = html.replace('\\"', '"')
    start = text.find(marker)
    if start >= 0:
        profile, _ = decoder.raw_decode(text[start + len(marker) :])
        if isinstance(profile, dict):
            return profile

    raise ValueError("profile marker was not found")


def parse_stat_value(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return value
    return value


def stats_map(profile: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for row in profile.get("stats", []):
        stat_id = row.get("id")
        if stat_id:
            mapped[stat_id] = parse_stat_value(row.get("value"))
    return mapped


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    try:
        numerator_float = float(numerator)
        denominator_float = float(denominator)
    except (TypeError, ValueError):
        return None
    if denominator_float <= 0:
        return None
    value = numerator_float / denominator_float
    if math.isfinite(value):
        return value
    return None


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def round_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 4)


def flatten_profile(candidate: Candidate, profile: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    stats = stats_map(profile)
    row = {field: None for field in CSV_FIELDS}
    row.update(
        {
            "source": "stalzone_wiki",
            "region": normalize_region(candidate.region),
            "nickname": candidate.nickname,
            "username": profile.get("username"),
            "uuid": profile.get("uuid"),
            "alliance": profile.get("alliance") or candidate.alliance,
            "profile_url": profile_url(candidate),
            "fetched_at": fetched_at,
            "last_login": profile.get("lastLogin"),
            "candidate_last_searched_at": candidate.last_searched_at,
            "candidate_search_count": candidate.search_count,
            "notes": candidate.candidate_source,
        }
    )

    for field, stat_id in STAT_IDS.items():
        if stat_id in stats:
            row[field] = stats[stat_id]

    row["kd_raw"] = safe_ratio(row["kills"], row["deaths"])
    row["accuracy_pct"] = round_pct(safe_ratio(row["shots_hit"], row["shots_fired"]))
    row["headshot_hit_pct"] = round_pct(safe_ratio(row["hit_head"], row["shots_hit"]))
    row["body_hit_pct"] = round_pct(safe_ratio(row["hit_body"], row["shots_hit"]))
    row["limb_hit_pct"] = round_pct(safe_ratio(row["hit_limbs"], row["shots_hit"]))
    return row


def should_keep_by_last_login(row: dict[str, Any], max_last_login_days: int | None) -> bool:
    if max_last_login_days is None:
        return True
    last_login = iso_to_datetime(row.get("last_login"))
    if last_login is None:
        return False
    age = datetime.now(timezone.utc) - last_login.astimezone(timezone.utc)
    return age.days <= max_last_login_days


def write_jsonl(path: Path, payloads: list[dict[str, Any]], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as file:
        for payload in payloads:
            file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            file.write("\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def collect(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queries = list(dict.fromkeys([*args.query, *discovery_queries(args.discover_prefixes)]))
    candidates = fetch_candidates(args.popular_period, args.recent, queries)
    if args.shuffle_candidates:
        rng = random.Random(args.random_seed)
        rng.shuffle(candidates)
    if args.limit:
        candidates = candidates[: args.limit]

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    raw_payloads: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates, start=1):
        if args.target_rows and len(rows) >= args.target_rows:
            break
        url = profile_url(candidate)
        try:
            html = fetch_text(url)
            profile = extract_profile(html)
            row = flatten_profile(candidate, profile, fetched_at)
            raw_payloads.append(
                {
                    "source": "stalzone_wiki",
                    "candidate": candidate.__dict__,
                    "profile_url": url,
                    "fetched_at": fetched_at,
                    "profile": profile,
                }
            )
            if should_keep_by_last_login(row, args.max_last_login_days):
                rows.append(row)
            print(f"[{index}/{len(candidates)}] ok {candidate.region}/{candidate.nickname}", file=sys.stderr)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raw_payloads.append(
                {
                    "source": "stalzone_wiki",
                    "candidate": candidate.__dict__,
                    "profile_url": url,
                    "fetched_at": fetched_at,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[{index}/{len(candidates)}] fail {candidate.region}/{candidate.nickname}: {exc}", file=sys.stderr)
        if args.delay_seconds > 0:
            time.sleep(args.delay_seconds)

    return rows, raw_payloads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect STALZONE combat player statistics.")
    parser.add_argument("--out-csv", type=Path, default=Path("data/combat_player_stats.csv"))
    parser.add_argument("--out-jsonl", type=Path, default=Path("data/combat_player_stats_raw.jsonl"))
    parser.add_argument("--limit", type=int, default=40, help="Maximum candidate profiles to fetch.")
    parser.add_argument("--recent", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--popular-period", action="append", default=["week", "month", "all"])
    parser.add_argument("--query", action="append", default=[], help="Extra character suggestion query.")
    parser.add_argument(
        "--discover-prefixes",
        choices=["none", "basic", "balanced", "wide"],
        default="none",
        help="Generate extra character suggestion queries. balanced is usually enough for a few hundred profiles.",
    )
    parser.add_argument("--target-rows", type=int, default=0, help="Stop profile fetching after this many kept rows.")
    parser.add_argument("--shuffle-candidates", action="store_true", help="Shuffle candidates before applying limit.")
    parser.add_argument("--random-seed", type=int, default=20260721)
    parser.add_argument("--append-raw", action="store_true", help="Append raw JSONL instead of replacing it.")
    parser.add_argument(
        "--max-last-login-days",
        type=int,
        default=365,
        help="Keep only rows with lastLogin no older than this many days. Use -1 to disable.",
    )
    parser.add_argument("--delay-seconds", type=float, default=0.2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_last_login_days < 0:
        args.max_last_login_days = None
    rows, raw_payloads = collect(args)
    write_jsonl(args.out_jsonl, raw_payloads, append=args.append_raw)
    write_csv(args.out_csv, rows)
    print(f"saved rows={len(rows)} raw_payloads={len(raw_payloads)} csv={args.out_csv} jsonl={args.out_jsonl}")


if __name__ == "__main__":
    main()
