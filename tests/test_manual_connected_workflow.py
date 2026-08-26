import unittest
from pathlib import Path
from unittest.mock import patch

from compliance import operation_policy
from golden.revalidate import revalidate_golden


ROOT = Path(__file__).resolve().parents[1]


class ManualConnectedPolicyTests(unittest.TestCase):
    def test_operator_triggered_reads_are_allowed(self):
        for action in (
            "creator_center_collect",
            "platform_collect",
            "platform_search",
            "platform_detail_fetch",
            "platform_revalidate",
            "verification_session",
        ):
            with self.subTest(action=action):
                self.assertTrue(operation_policy.check(action)["allowed"])

    def test_interaction_and_publish_stay_blocked(self):
        self.assertFalse(operation_policy.check("platform_interaction")["allowed"])
        self.assertFalse(operation_policy.check("platform_publish")["allowed"])

    def test_dashboard_has_connected_actions_and_no_manual_import_panel(self):
        html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
        for marker in (
            'onclick="doDataCenterCollect()"',
            'onclick="doCollect()"',
            'onclick="collectExternalStage()"',
            'onclick="doGoldenRevalidate()"',
                'onclick="runFullDaily()"',
        ):
            self.assertIn(marker, html)
        removed_api_prefix = "/api/" + "off" + "line"
        removed_panel_id = 'id="s-' + "off" + 'line"'
        self.assertNotIn(removed_api_prefix, html)
        self.assertNotIn(removed_panel_id, html)

    def test_flask_routes_do_not_expose_removed_import_api(self):
        from app import app

        routes = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/api/account/data-center/collect", routes)
        self.assertIn("/api/golden/revalidate", routes)
        self.assertIn("/api/daily/run", routes)
        removed_api_prefix = "/api/" + "off" + "line"
        self.assertFalse(any(route.startswith(removed_api_prefix) for route in routes))


class GoldenRevalidationTests(unittest.TestCase):
    @patch("golden.revalidate.repository.update_revalidation")
    @patch(
        "golden.revalidate.repository.list_heat_snapshots",
        return_value=[
            {"observed_at": "2026-08-17", "likes": 100, "collects": 20, "comments": 5},
            {"observed_at": "2026-08-19", "likes": 130, "collects": 35, "comments": 9},
        ],
    )
    @patch("golden.revalidate.repository.record_heat_snapshot")
    @patch("golden.revalidate.repository.now", return_value="2026-08-19T12:00:00")
    @patch(
        "golden.revalidate._detail_to_fields",
        return_value={"likes": 130, "collects": 35, "comments": 9},
    )
    @patch("golden.revalidate.collect.run_cli", return_value=({"data": {}}, None))
    @patch(
        "golden.revalidate.repository.list_samples",
        return_value=[
            {
                "golden_id": "gs_test",
                "title": "测试样本",
                "url": "https://www.xiaohongshu.com/explore/test",
                "created_at": "2020-01-01T00:00:00",
                "last_revalidated_at": "",
            }
        ],
    )
    def test_revalidation_fetches_live_metrics_only_when_called(
        self,
        _samples,
        run_cli,
        _fields,
        _now,
        record_snapshot,
        _snapshots,
        update_revalidation,
    ):
        result = revalidate_golden(limit=10)

        self.assertEqual(result["mode"], "live_user_triggered")
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["revalidated"], 1)
        run_cli.assert_called_once_with(
            ["note", "https://www.xiaohongshu.com/explore/test"], retries=0
        )
        record_snapshot.assert_called_once()
        update_revalidation.assert_called_once()


if __name__ == "__main__":
    unittest.main()
