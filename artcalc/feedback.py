"""Contextual preference memory and pairwise ranking, independent of the solver."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

import numpy as np


REASONS = {"balance", "price", "composition", "availability", "upgrade", "data_error"}


def context_similarity(left: dict, right: dict) -> float:
    for key in ("armor_id", "container_id", "max_quality_tier", "active_reaction"):
        if left.get(key) != right.get(key):
            return 0.0
    a, b = left.get("budget"), right.get("budget")
    if (a is None) != (b is None):
        return 0.0
    if set(left.get("targets", {})) != set(right.get("targets", {})):
        return 0.0
    distance = abs(math.log(max(a, 1) / max(b, 1))) if a is not None else 0.0
    for key, target in left.get("targets", {}).items():
        other = right["targets"][key]
        distance += abs(target - other) / max(abs(target), abs(other), 10)
    return math.exp(-4 * distance)


def features(build: dict, context: dict) -> np.ndarray:
    stats, derived = build.get("stats", {}), build.get("derived", {})
    artifacts = build.get("artifacts", [])
    n = max(len(artifacts), 1)
    targets = context.get("targets", {})
    price = build.get("total_price", 0) / max(context.get("budget") or 1, 1) if context.get("budget") is not None else 0
    values = [
        (derived.get("durability_with_reaction", derived.get("effective_durability", 100)) - targets.get("durability", 100)) / 200,
        (stats.get("movement_speed", 0) - targets.get("speed", 0)) / 20,
        derived.get("hp_regen_score", 0) / 20,
        stats.get("healing_effectiveness", 0) / 50,
        stats.get("carry_weight", 0) / 200,
        price,
        sum(abs(a.get("quality_percent", 100) - round(a.get("quality_percent", 100))) > 0.001 for a in artifacts) / n,
        sum(a.get("price_estimate", {}).get("confidence") in ("low", "very_low", "none") for a in artifacts) / n,
        build.get("upgrade_potential", {}).get("score", 0) / 100,
    ]
    return np.clip(np.asarray(values, dtype=float), -3, 3)


def composition_similarity(left: dict, right: dict) -> float:
    a = Counter(x["item_id"] for x in left.get("artifacts", []))
    b = Counter(x["item_id"] for x in right.get("artifacts", []))
    keys = a.keys() | b.keys()
    union = sum(max(a[k], b[k]) for k in keys)
    return sum(min(a[k], b[k]) for k in keys) / max(union, 1)


class FeedbackRanker:
    """Use local examples first; fit regularized pairwise preferences when present.

    No candidate is generated, removed or made valid here. Data-error reports
    are deliberately excluded from training. Prices vanish in unlimited mode.
    """

    def rank(self, candidates: list[dict], context: dict, events: list[dict]) -> list[dict]:
        examples = [(event, context_similarity(context, event["context"])) for event in events
                    if event["reason"] != "data_error"
                    and not (context.get("budget") is None and event["reason"] == "price")]
        examples = [(e, weight) for e, weight in examples if weight > 0.1]
        if not examples:
            return candidates
        weights = np.zeros(9)
        pairs = [(features(e["build"], context) - features(e["other"], context), similarity)
                 for e, similarity in examples if e.get("other")]
        if pairs:
            for _ in range(30):
                gradient = -0.5 * weights
                for difference, similarity in pairs:
                    probability = 1 / (1 + math.exp(float(np.clip(weights @ difference, -30, 30))))
                    gradient += similarity * probability * difference / len(pairs)
                weights += 0.2 * gradient
        ranked = []
        for index, candidate in enumerate(candidates):
            vector = features(candidate, context)
            score = 0.5 * math.tanh(float(weights @ vector))
            mass = 0.0
            local_score = 0.0
            for event, similarity in examples:
                examples_in_event = [(event["build"], event["rating"])]
                if event.get("other"):
                    examples_in_event.append((event["other"], -1))
                for example, rating in examples_in_event:
                    delta = vector - features(example, context)
                    if event["reason"] == "price":
                        distance = float(delta[:2] @ delta[:2] + 3 * delta[5] ** 2)
                    elif event["reason"] == "availability":
                        distance = float(delta[6:8] @ delta[6:8])
                    elif event["reason"] == "upgrade":
                        distance = float(delta[:2] @ delta[:2] + 3 * delta[8] ** 2)
                    else:
                        distance = float(delta[:5] @ delta[:5])
                    match = composition_similarity(candidate, example)
                    if event["reason"] == "composition":
                        distance += 8 * (1 - match)
                    else:
                        distance += 2 * (1 - match)
                    weight = similarity * math.exp(-2 * distance)
                    local_score += rating * weight
                    mass += weight
            score += local_score / (1 + mass)
            # Preserve a small stable prior and the complete diverse portfolio.
            ranked.append((score - index * 0.015, index, candidate))
        return [candidate for _, _, candidate in sorted(ranked, key=lambda row: (-row[0], row[1]))]


class FeedbackStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS searches (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL, created REAL NOT NULL,
                    context TEXT NOT NULL, candidates TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS search_profile ON searches(profile, created);
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL, event_key TEXT NOT NULL,
                    created REAL NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(profile, event_key));
                CREATE INDEX IF NOT EXISTS feedback_profile ON feedback(profile, created);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def remember(self, profile: str, context: dict, candidates: list[dict]) -> str:
        search_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO searches VALUES (?, ?, ?, ?, ?)",
                       (search_id, profile, time.time(), json.dumps(context), json.dumps(candidates)))
            db.execute("DELETE FROM searches WHERE created < ?", (time.time() - 30 * 86400,))
            db.execute("DELETE FROM searches WHERE profile=? AND id NOT IN "
                       "(SELECT id FROM searches WHERE profile=? ORDER BY created DESC LIMIT 100)", (profile, profile))
        return search_id

    def record(self, profile: str, search_id: str, build_id: str, rating: int,
               reason: str, other_id: str | None = None) -> str:
        if reason not in REASONS or rating not in (-1, 1):
            raise ValueError("Invalid feedback")
        with self.connect() as db:
            row = db.execute("SELECT context, candidates FROM searches WHERE id=? AND profile=?",
                             (search_id, profile)).fetchone()
            if row is None:
                raise ValueError("Search not found or expired")
            context = json.loads(row[0])
            candidates = {s["build_id"]: s for s in json.loads(row[1])}
            if build_id not in candidates or (other_id is not None and other_id not in candidates):
                raise ValueError("Build was not returned by this search")
            if other_id == build_id or (other_id and (rating != 1 or reason == "data_error")):
                raise ValueError("Invalid comparison")
            event = {"context": context, "build": candidates[build_id], "rating": rating,
                     "reason": reason, "other": candidates.get(other_id)}
            key = hashlib.sha256(json.dumps([context, build_id, other_id], sort_keys=True).encode()).hexdigest()
            event_id = uuid.uuid4().hex
            db.execute("INSERT INTO feedback VALUES (?, ?, ?, ?, ?) ON CONFLICT(profile,event_key) "
                       "DO UPDATE SET id=excluded.id,created=excluded.created,payload=excluded.payload",
                       (event_id, profile, key, time.time(), json.dumps(event)))
            db.execute("DELETE FROM feedback WHERE profile=? AND id NOT IN "
                       "(SELECT id FROM feedback WHERE profile=? ORDER BY created DESC LIMIT 1000)", (profile, profile))
            return event_id

    def events(self, profile: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id, payload FROM feedback WHERE profile=? ORDER BY created", (profile,)).fetchall()
        return [{**json.loads(row[1]), "id": row[0]} for row in rows]

    def undo(self, profile: str, event_id: str) -> bool:
        with self.connect() as db:
            return db.execute("DELETE FROM feedback WHERE profile=? AND id=?", (profile, event_id)).rowcount > 0
