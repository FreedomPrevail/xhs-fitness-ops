import sqlite3
import tempfile
import unittest

from analyze import account_intelligence
from collect import creator_data_center
from collect import collect


class CreatorDataCenterTest(unittest.TestCase):
    def test_percent_fields_reach_database_scale(self):
        self.assertAlmostEqual(collect.percent_value("7.2%"), .072)
        self.assertEqual(collect.percent_value(0.072), .072)
        self.assertIsNone(collect.percent_value(None))

    def test_visible_dom_metrics_are_typed_without_fabrication(self):
        capture = {"page_text": """
账号数据
观看数
12,345
曝光数 20,000
新增粉丝 23
分享数 18
点击率 6.5%
平均观看时长 7.4秒
粉丝活跃时间
19:00-22:00
性别分布：女性 70%，男性 30%
"""}
        result = creator_data_center.normalize_capture(capture)
        self.assertEqual(result["account_metrics"]["views"], 12345)
        self.assertEqual(result["account_metrics"]["impressions"], 20000)
        self.assertEqual(result["account_metrics"]["rise_fans"], 23)
        self.assertEqual(result["account_metrics"]["shares"], 18)
        self.assertAlmostEqual(result["account_metrics"]["ctr"], .065)
        self.assertAlmostEqual(result["account_metrics"]["avg_view_time_seconds"], 7.4)
        self.assertEqual(result["active_periods"], ["19:00-22:00"])
        self.assertNotIn("completion_rate", result["account_metrics"])

    def test_snapshot_round_trip_preserves_missing_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            con = sqlite3.connect(tmp.name)
            account_intelligence.save_data_center_snapshot(con, {
                "source": "creator_data_center_dom",
                "account_metrics": {"views": 100, "ctr": .12},
                "metric_evidence": {"views": {"label": "观看数", "raw": "100"}},
                "pages": [{"url": creator_data_center.ACCOUNT_URL, "kind": "account"}],
                "url": creator_data_center.ACCOUNT_URL,
            })
            row = account_intelligence.latest_data_center_snapshot(con)
            con.close()
        self.assertEqual(row["account_metrics"], {"views": 100, "ctr": .12})
        self.assertNotIn("completion_rate", row["account_metrics"])
        self.assertEqual(row["page_count"], 1)

    def test_visible_note_table_becomes_attributable_note_metrics(self):
        capture = {
            "url": "https://creator.xiaohongshu.com/statistics/content",
            "tables": [{
                "headers": ["笔记标题", "观看数", "收藏数", "分享数", "平均观看时长", "点击率"],
                "rows": [[
                    {"text": "新手晚间训练", "href": "https://www.xiaohongshu.com/explore/abc123"},
                    {"text": "1,200", "href": ""}, {"text": "88", "href": ""},
                    {"text": "12", "href": ""}, {"text": "9.5秒", "href": ""},
                    {"text": "7.2%", "href": ""},
                ]],
            }],
        }
        notes = creator_data_center.normalize_note_tables(capture)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note_id"], "abc123")
        self.assertEqual(notes[0]["views"], 1200)
        self.assertEqual(notes[0]["favorites"], 88)
        self.assertEqual(notes[0]["shares"], 12)
        self.assertAlmostEqual(notes[0]["avg_view_time"], 9.5)
        self.assertAlmostEqual(notes[0]["ctr"], .072)


if __name__ == "__main__":
    unittest.main()
