import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import app as service


class FunnelDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        service.DB_PATH = Path(cls.temp.name) / "test.db"
        cls.seed_result = service.seed_database(force=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_seed_has_actual_and_forecast_rows(self):
        self.assertGreater(self.seed_result["actual_rows"], 0)
        self.assertGreater(self.seed_result["forecast_rows"], 0)
        now = service.now_local()
        with service.connect() as con:
            bounds = con.execute(
                "SELECT bucket_type, MIN(occurred_at), MAX(occurred_at), COUNT(*) "
                "FROM traffic_facts GROUP BY bucket_type"
            ).fetchall()
        by_type = {row[0]: row for row in bounds}
        actual_min = datetime.fromisoformat(by_type["actual"][1])
        forecast_max = datetime.fromisoformat(by_type["forecast"][2])
        self.assertGreaterEqual(actual_min, now - timedelta(days=60, hours=1))
        self.assertLessEqual(forecast_max, now + timedelta(days=7))
        self.assertGreaterEqual(datetime.fromisoformat(by_type["actual"][2]), now - timedelta(hours=2))

    def test_overview_uses_actual_facts_only(self):
        result = service.overview(30)
        self.assertGreater(result["current"]["sessions"], 0)
        self.assertGreater(result["current"]["orders"], 0)
        self.assertGreater(result["forecast"]["sessions"], 0)
        self.assertEqual(len(result["funnel"]), 6)
        self.assertLessEqual(result["funnel"][-1]["count"], result["funnel"][0]["count"])

    def test_diagnostic_is_ranked_and_persisted(self):
        result = service.execute_diagnostic("test")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertEqual([h["rank"] for h in result["hypotheses"]], [1, 2, 3])
        self.assertEqual(service.latest_diagnostic()["id"], result["id"])

    def test_trend_returns_daily_actuals(self):
        result = service.trend(30)
        self.assertGreater(len(result["points"]), 20)
        self.assertTrue(all(point["sessions"] >= point["orders"] for point in result["points"]))


if __name__ == "__main__":
    unittest.main()
