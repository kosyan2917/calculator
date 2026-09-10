from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from artcalc.feedback import FeedbackRanker, FeedbackStore, context_similarity, features


def build(name, speed, durability):
    return {"build_id": name, "stats": {"movement_speed": speed},
            "derived": {"effective_durability": durability}, "total_price": 10_000_000,
            "artifacts": [{"item_id": name, "quality_percent": 130}], "infection": {"valid": True}}


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.context = {"budget": 15_000_000, "armor_id": "armor", "container_id": "bag",
                        "max_quality_tier": "exclusive", "targets": {"durability": 450}}
        self.tank, self.fast = build("tank", -5, 560), build("fast", 10, 470)

    def test_negative_feedback_reranks_without_removing_or_mutating_builds(self):
        candidates = [self.tank, self.fast]
        before = deepcopy(candidates)
        event = {"context": self.context, "build": self.tank, "rating": -1, "reason": "balance"}
        result = FeedbackRanker().rank(candidates, self.context, [event])
        self.assertEqual(result[0]["build_id"], "fast")
        self.assertEqual(candidates, before)
        self.assertEqual({b["build_id"] for b in result}, {"tank", "fast"})

    def test_comparison_favors_winner(self):
        event = {"context": self.context, "build": self.fast, "other": self.tank, "rating": 1, "reason": "balance"}
        result = FeedbackRanker().rank([self.tank, self.fast], self.context, [event])
        self.assertEqual(result[0]["build_id"], "fast")

    def test_data_errors_do_not_train_ranking(self):
        candidates = [self.tank, self.fast]
        event = {"context": self.context, "build": self.tank, "rating": -1, "reason": "data_error"}
        self.assertEqual(FeedbackRanker().rank(candidates, self.context, [event]), candidates)

    def test_other_query_context_does_not_transfer_blindly(self):
        changed = {**self.context, "targets": {"speed": 15}}
        self.assertEqual(context_similarity(self.context, changed), 0)

    def test_unlimited_features_ignore_purchase_price(self):
        context = {**self.context, "budget": None}
        expensive = {**self.tank, "total_price": 10**12}
        self.assertTrue((features(self.tank, context) == features(expensive, context)).all())

    def test_storage_is_private_deduplicated_persistent_and_reversible(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.sqlite3"
            store = FeedbackStore(path)
            search = store.remember("alice", self.context, [self.tank, self.fast])
            with self.assertRaises(ValueError):
                store.record("bob", search, "tank", -1, "balance")
            with self.assertRaises(ValueError):
                store.record("alice", search, "fabricated", -1, "balance")
            store.record("alice", search, "tank", -1, "balance")
            event = store.record("alice", search, "tank", 1, "composition")
            self.assertEqual(len(store.events("alice")), 1)
            self.assertEqual(store.events("bob"), [])
            store = FeedbackStore(path)
            self.assertEqual(store.events("alice")[0]["rating"], 1)
            self.assertFalse(store.undo("bob", event))
            self.assertTrue(store.undo("alice", event))
            self.assertEqual(store.events("alice"), [])
