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
            "playlist_track_count", "mixed_order_count", "track_attributes",
        }
        self.assertEqual(set(profile.sql_backend.registered_metric_ids), expected)
        self.assertEqual({item.metric_id for item in profile.metric_definitions}, expected)
        self.assertTrue(all(item.unit and item.source_refs for item in profile.metric_definitions))

    def test_canonical_catalog_has_all_core_metrics(self):
        payload = json.loads(Path("profiles/chinook/metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["metrics"]), 15)
        self.assertEqual({metric["metric_id"] for metric in payload["metrics"]}, set(ProfileRegistry.defaults().get("chinook-music").sql_backend.registered_metric_ids))
        attributes=next(metric for metric in payload['metrics'] if metric['metric_id']=='track_attributes')
        self.assertEqual(attributes['required_slots'],[])
        self.assertEqual(attributes['aggregation'],'catalog_attributes')
        self.assertIn('main.Track.UnitPrice',attributes['required_columns'])
        self.assertFalse(any('Invoice' in column for column in attributes['required_columns']))


if __name__ == "__main__":
    unittest.main()
