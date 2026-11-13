import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app as service


def metric_row(**overrides):
    row = {
        "sessions": 100_000,
        "product_views": 78_000,
        "add_to_cart": 23_000,
        "checkout_started": 17_000,
        "payment_attempts": 14_000,
        "orders": 11_000,
        "revenue": 33_000_000.0,
        "payment_failures": 3_000,
        "avg_page_load_ms": 2_200.0,
        "out_of_stock_sessions": 2_000,
    }
    row.update(overrides)
    return row


def mocked_aggregate(scenario="neutral", low_sample=False):
    now = datetime(2026, 11, 13, 14, 30, 0)

    def fake(start, end, filters=None):
        current = end == now
        filters = filters or {}
        if filters.get("device") == "Android App" and "payment_method" in filters:
            attempts = 100 if low_sample else 12_000
            previous_orders = round(attempts * 0.85)
            current_rate = 0.45 if scenario == "anomaly" else 0.77 if scenario == "threshold" else 0.85
            current_orders = round(attempts * current_rate)
            if not current:
                return metric_row(sessions=20_000, payment_attempts=attempts, orders=previous_orders)
            return metric_row(sessions=20_000, payment_attempts=attempts, orders=current_orders)

        if filters.get("device") == "Android App":
            sessions = 400 if low_sample else 62_000
            if not current:
                return metric_row(sessions=sessions, product_views=48_000,
                                  add_to_cart=14_400, avg_page_load_ms=2_200.0)
            return metric_row(
                sessions=sessions,
                product_views=48_000,
                add_to_cart=13_680 if scenario == "anomaly" else 14_400,
                avg_page_load_ms=3_500.0 if scenario == "anomaly" else 2_200.0,
            )

        if filters.get("channel") == "Paid Social":
            sessions = 450 if low_sample else (35_000 if current and scenario == "anomaly" else 25_000)
            return metric_row(sessions=sessions, product_views=round(sessions * 0.56))

        if filters.get("channel") == "Organic Search":
            return metric_row(sessions=39_000, product_views=30_420)

        if filters.get("channel") == "Paid Search":
            return metric_row(sessions=26_000, product_views=18_720)

        return metric_row()

    return fake, now


class FunnelDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        service.DB_PATH = Path(cls.temp.name) / "test.db"
        cls.seed_result = service.seed_database(force=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_seed_has_relative_actual_and_forecast_windows(self):
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
        actual_max = datetime.fromisoformat(by_type["actual"][2])
        forecast_min = datetime.fromisoformat(by_type["forecast"][1])
        forecast_max = datetime.fromisoformat(by_type["forecast"][2])
        self.assertGreaterEqual(actual_min, now - timedelta(days=60, hours=1))
        self.assertLessEqual(actual_min, now - timedelta(days=60))
        self.assertGreaterEqual(actual_max, now - timedelta(hours=2))
        self.assertGreaterEqual(forecast_min, now.replace(minute=0, second=0, microsecond=0))
        self.assertLessEqual(forecast_max, now + timedelta(days=7))
        self.assertGreaterEqual(forecast_max, now + timedelta(days=6, hours=22))

    def test_forecast_rows_never_leak_into_actual_kpis(self):
        now = service.now_local()
        result = service.overview(30)
        expected = service.aggregate(now - timedelta(days=30), now)
        self.assertEqual(result["current"]["sessions"], expected["sessions"])
        self.assertEqual(result["current"]["orders"], expected["orders"])
        self.assertGreater(result["forecast"]["sessions"], 0)
        with service.connect() as con:
            total_including_forecast = con.execute("SELECT SUM(sessions) FROM traffic_facts").fetchone()[0]
        self.assertLess(result["current"]["sessions"], total_including_forecast)

    def test_seeded_conversion_delta_matches_demo_scenario(self):
        result = service.overview(30)
        self.assertLess(result["conversion_delta_pp"], -1.5)
        self.assertGreater(result["conversion_delta_pp"], -2.5)

    def test_overview_funnel_is_monotonic_and_percentages_are_bounded(self):
        result = service.overview(30)
        funnel = result["funnel"]
        self.assertEqual(len(funnel), 6)
        counts = [stage["count"] for stage in funnel]
        self.assertEqual(counts, sorted(counts, reverse=True))
        for stage in funnel:
            self.assertGreaterEqual(stage["share_of_visits"], 0)
            self.assertLessEqual(stage["share_of_visits"], 100)
            self.assertGreaterEqual(stage["drop_from_previous"], 0)
            self.assertLessEqual(stage["drop_from_previous"], 100)

    def test_supported_date_windows_work_at_limits(self):
        self.assertGreater(service.overview(7)["current"]["sessions"], 0)
        self.assertGreater(service.overview(60)["current"]["sessions"], 0)
        self.assertGreater(len(service.trend(7)["points"]), 0)
        self.assertGreater(len(service.trend(60)["points"]), 0)

    def test_aggregate_uses_inclusive_start_and_exclusive_end(self):
        start = service.now_local() - timedelta(days=2)
        end = start + timedelta(hours=1)
        before = service.aggregate(start, end)["sessions"]
        with service.connect() as con:
            con.execute(
                """
                INSERT INTO traffic_facts (
                  occurred_at,bucket_type,channel,device,payment_method,sessions,
                  product_views,add_to_cart,checkout_started,payment_attempts,orders,
                  revenue,page_load_ms,payment_failures,out_of_stock_sessions
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (service.iso(end), "actual", "Organic Search", "Web", "UPI",
                 1000, 700, 200, 100, 90, 70, 210000, 1000, 20, 10),
            )
        after = service.aggregate(start, end)["sessions"]
        self.assertEqual(before, after)

    def test_unknown_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            service.aggregate(service.now_local() - timedelta(days=1), service.now_local(),
                              {"channel; DROP TABLE traffic_facts": "x"})

    def test_zero_denominator_is_safe(self):
        self.assertEqual(service.pct(0, 0), 0)
        self.assertEqual(service.conversion(metric_row(sessions=0, orders=0)), 0)

    def test_seeded_diagnostic_detects_the_designed_signals(self):
        hypotheses = service.make_hypotheses()
        self.assertEqual(len(hypotheses), 3)
        self.assertTrue(all(item["signal_detected"] for item in hypotheses))
        self.assertIn("payment", {item["key"] for item in hypotheses})
        self.assertIn("latency", {item["key"] for item in hypotheses})
        self.assertIn("mix", {item["key"] for item in hypotheses})
        self.assertEqual([item["rank"] for item in hypotheses], [1, 2, 3])

    def test_neutral_metrics_do_not_create_false_positive_signals(self):
        fake, now = mocked_aggregate("neutral")
        with patch.object(service, "aggregate", side_effect=fake):
            hypotheses = service.make_hypotheses(now)
        self.assertTrue(all(not item["signal_detected"] for item in hypotheses))
        self.assertTrue(all(item["confidence"] == "Low" for item in hypotheses))
        self.assertTrue(all(item["signal_label"] == "Watch only" for item in hypotheses))

    def test_material_anomalies_are_not_missed(self):
        fake, now = mocked_aggregate("anomaly")
        with patch.object(service, "aggregate", side_effect=fake):
            hypotheses = service.make_hypotheses(now)
        detected = {item["key"] for item in hypotheses if item["signal_detected"]}
        self.assertEqual(detected, {"payment", "latency", "mix"})

    def test_small_samples_do_not_trigger_false_alarms(self):
        fake, now = mocked_aggregate("anomaly", low_sample=True)
        with patch.object(service, "aggregate", side_effect=fake):
            hypotheses = service.make_hypotheses(now)
        self.assertTrue(all(not item["signal_detected"] for item in hypotheses))
        self.assertTrue(all(item["confidence"] == "Low" for item in hypotheses))

    def test_payment_threshold_is_inclusive_at_eight_percentage_points(self):
        fake, now = mocked_aggregate("threshold")
        with patch.object(service, "aggregate", side_effect=fake):
            hypotheses = service.make_hypotheses(now)
        payment = next(item for item in hypotheses if item["key"] == "payment")
        self.assertTrue(payment["signal_detected"])

    def test_diagnostic_is_ranked_and_persisted(self):
        result = service.execute_diagnostic("test")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertEqual([h["rank"] for h in result["hypotheses"]], [1, 2, 3])
        self.assertEqual(service.latest_diagnostic()["id"], result["id"])
        self.assertEqual(service.latest_diagnostic()["trigger_type"], "test")

    def test_run_history_is_queryable(self):
        result = service.execute_diagnostic("scheduled")
        history = service.runs(5)
        self.assertGreaterEqual(len(history["runs"]), 1)
        self.assertEqual(history["runs"][0]["id"], result["id"])
        self.assertEqual(history["runs"][0]["trigger_type"], "scheduled")

    def test_live_event_stream_appends_an_actual_bucket(self):
        with service.connect() as con:
            before = con.execute("SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='actual'").fetchone()[0]
        service.insert_live_event()
        with service.connect() as con:
            after = con.execute("SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='actual'").fetchone()[0]
            latest_type = con.execute("SELECT bucket_type FROM traffic_facts ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(after, before + 1)
        self.assertEqual(latest_type, "actual")

    def test_reseed_resets_agent_history_and_restores_data(self):
        service.execute_diagnostic("before-reseed")
        self.assertIsNotNone(service.latest_diagnostic())
        result = service.seed_database(force=True)
        self.assertIsNone(service.latest_diagnostic())
        self.assertGreater(result["actual_rows"], 0)
        self.assertGreater(result["forecast_rows"], 0)

    def test_health_reports_actual_forecast_and_run_counts(self):
        service.execute_diagnostic("health-test")
        result = service.health()
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["actual_rows"], 0)
        self.assertGreater(result["forecast_rows"], 0)
        self.assertGreaterEqual(result["diagnostic_runs"], 1)


if __name__ == "__main__":
    unittest.main()
