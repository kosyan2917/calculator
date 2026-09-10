import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from artcalc.loaders import load_artifact_price_segments
from tools.staldata_prices import local_basis


class MarketPriceTests(unittest.TestCase):
    def test_aligned_windows_override_legacy_sales_counts(self):
        price, basis, confidence = local_basis({
            "fair_price": 100, "fair_price_source": "raw_7d", "sales_7d": 100,
            "aligned_sales_windows": {"sales_7d": 1, "sales_30d": 10}}, 5)
        self.assertEqual((price, basis, confidence), (100, "recent_30d", "high"))

    def test_lower_upgrades_are_not_substituted_for_plus15(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            path.write_text(json.dumps({"segments": [
                {"item_id": "a", "quality_tier": "rare", "upgrade_level": 0, "price": 10},
                {"item_id": "a", "quality_tier": "rare", "upgrade_level": 14, "price": 40},
                {"item_id": "b", "quality_tier": "rare", "upgrade_level": 15, "price": 50},
            ]}), encoding="utf-8")
            rows = load_artifact_price_segments(path, 15)
            self.assertFalse(rows[("a", "rare")]["price_estimate"]["available"])
            self.assertEqual(rows[("a", "rare")]["price_estimate"]["nearby_upgrades"][0]["price"], 40)
            self.assertEqual(rows[("b", "rare")]["price"], 50)
