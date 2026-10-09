import json
from pathlib import Path
import unittest

from services.agent_api.app.profiles import ProfileRegistry


class ChinookMetricTests(unittest.TestCase):
    def test_profile_registers_all_core_metrics(self):
        profile = ProfileRegistry.defaults().get("chinook-music")
        expected = {
            "customer_count",
            "sales_amount", "units_sold", "order_count", "purchasing_customers",
            "average_order_value", "genre_sales", "track_sales", "album_sales",
            "artist_sales", "media_type_sales", "billing_country_sales",
            "playlist_track_count",
        }
        self.assertEqual(set(profile.sql_backend.registered_metric_ids), expected)
        self.assertEqual({item.metric_id for item in profile.metric_definitions}, expected)
        self.assertTrue(all(item.unit and item.source_refs for item in profile.metric_definitions))

    def test_canonical_catalog_has_all_core_metrics(self):
        payload = json.loads(Path("profiles/chinook/metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["metrics"]), 13)
        self.assertEqual({metric["metric_id"] for metric in payload["metrics"]}, set(ProfileRegistry.defaults().get("chinook-music").sql_backend.registered_metric_ids))


if __name__ == "__main__":
    unittest.main()
