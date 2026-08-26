import copy
import json
import tempfile
import unittest
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from analyze import analyze
from analyze import discover
from analyze import heat, account_intelligence
from analyze import pipeline as topic_pipeline
from agents.topic import agent as topic_agent


class UnifiedTopicScoreTest(unittest.TestCase):
    def payload(self):
        return {
            "version": 1,
            "topics": [{
                "id": "t1", "label": "测试选题", "category": "fitness_value",
                "base_weight": 1.0, "account_weight": 1.0,
                "account_score_model": 0.2, "novelty_score": 0.6,
                "account_evidence_observed": True,
            }],
        }

    def test_exact_3530305_formula_and_weight_alias(self):
        payload = self.payload()
        support = {"score": 0.8, "pattern_ids": ["gp_test"], "breakdown": {}}
        with patch("golden.retriever.pattern_support", return_value=support):
            result = analyze.apply_unified_scores(payload, bump_version=False)
        topic = result["topics"][0]
        # sample.db没有平台证据时平台分为中性0.5：.2*.35+.5*.30+.8*.30+.6*.05=.49
        self.assertEqual(topic["final_score"], 0.49)
        self.assertEqual(topic["weight"], topic["final_score"])
        self.assertEqual(topic["score_breakdown"], {
            "account": 0.07, "platform": 0.15, "golden": 0.24, "novelty": 0.03,
        })
        self.assertEqual(result["scoring_formula"], {
            "account": 0.35, "platform": 0.30, "golden": 0.30, "novelty": 0.05,
        })

    def test_failed_gate_does_not_touch_official_file(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"topic_weights.json"
            original=self.payload(); path.write_text(json.dumps(original),encoding="utf-8")
            with patch.object(analyze,"WEIGHTS",path):
                with self.assertRaises(RuntimeError):
                    analyze.commit_unified_scores(original,"pipe_fail",{
                        "sample_rows":0,"golden_candidates":1,"golden_samples":1,
                        "golden_patterns":1,"account_evidence":1,
                    })
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),original)

    def test_only_complete_pipeline_commit_creates_official_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"topic_weights.json"
            original=self.payload(); path.write_text(json.dumps(original),encoding="utf-8")
            evidence={"sample_rows":30,"golden_candidates":10,"golden_features":8,
                      "golden_samples":6,"golden_patterns":3,"account_evidence":2}
            with patch.object(analyze,"WEIGHTS",path), \
                 patch("golden.retriever.pattern_support",return_value={"score":.8,"pattern_ids":["gp1"],"breakdown":{}}):
                result=analyze.commit_unified_scores(original,"pipe_ok",evidence)
            saved=json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(analyze.has_committed_score(saved))
            self.assertEqual(saved["score_commit"]["pipeline_id"],"pipe_ok")
            self.assertEqual(saved["score_commit"]["evidence"],evidence)
            self.assertEqual(saved["version"],2)
            self.assertEqual(saved["topics"][0]["weight"],saved["topics"][0]["final_score"])
            self.assertEqual(result,saved)

    def test_discovery_is_in_memory_until_pipeline_commit(self):
        original=self.payload()
        working=copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"topic_weights.json"
            path.write_text(json.dumps(original,ensure_ascii=False),encoding="utf-8")
            with patch.object(discover,"WEIGHTS",path):
                result=discover.merge_into_weights([{"label":"新发现训练角度","category":"fitness_value"}],weights=working)
            self.assertFalse(result["committed"])
            self.assertEqual(len(result["weights"]["topics"]),2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),original)

    def test_recommendation_refuses_uncommitted_scores(self):
        with patch.object(analyze,"load_weights",return_value=self.payload()):
            self.assertEqual(topic_agent.recommend(5),[])

    def test_inner_formulas_are_exact(self):
        self.assertEqual(account_intelligence.ACCOUNT_SCORE_WEIGHTS,{
            "historical_performance":.25,"interaction_structure":.15,
            "consumption_depth":.15,"follower_conversion":.10,
            "traffic_source":.10,"audience_fit":.15,"content_gap":.10,
        })
        self.assertEqual(heat.PLATFORM_HEAT_WEIGHTS,{
            "engagement_quality":.40,"trend_momentum":.25,
            "high_heat_sample_rate":.20,"sample_coverage":.15,
        })

    def test_platform_four_dimensions_and_real_growth_snapshot(self):
        con=sqlite3.connect(":memory:")
        con.execute("""CREATE TABLE samples(
          collected TEXT,keyword TEXT,title TEXT,author TEXT,likes_num INTEGER,
          collects_num INTEGER,comments_num INTEGER,detail_fetched INTEGER,
          published_at TEXT,collected_at TEXT,url TEXT,note_id TEXT)""")
        today=date.today(); yesterday=today-timedelta(days=1)
        rows=[
          (str(yesterday),"肩颈","肩颈拉伸","作者A",100,20,5,1,str(yesterday),str(yesterday),"u1","n1"),
          (str(today),"肩颈","肩颈拉伸","作者A",180,35,9,1,str(today),str(today),"u1","n1"),
          (str(today),"肩颈","久坐肩颈","作者B",90,10,3,1,str(today),str(today),"u2","n2"),
          (str(today),"减脂","低卡减脂","作者C",20,0,0,0,str(today),str(today),"u3","n3"),
        ]
        con.executemany("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",rows)
        result=heat.platform_heat("肩颈",con)
        self.assertEqual(set(result["dimensions"]),set(heat.PLATFORM_HEAT_WEIGHTS))
        self.assertIsNotNone(result["growth_score"])
        expected=sum(heat.PLATFORM_HEAT_WEIGHTS[k]*result["dimensions"][k] for k in heat.PLATFORM_HEAT_WEIGHTS)
        self.assertAlmostEqual(result["heat"],expected,places=2)
        con.close()

    def test_missing_growth_is_not_zero(self):
        con=sqlite3.connect(":memory:")
        con.execute("""CREATE TABLE samples(
          collected TEXT,keyword TEXT,title TEXT,author TEXT,likes_num INTEGER,
          collects_num INTEGER,comments_num INTEGER,detail_fetched INTEGER,
          published_at TEXT,collected_at TEXT,url TEXT,note_id TEXT)""")
        today=str(date.today())
        con.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (today,"拉伸","睡前拉伸","A",100,0,0,0,today,today,"u1","n1"))
        result=heat.platform_heat("拉伸",con)
        self.assertIsNone(result["growth_score"])
        self.assertEqual(result["dimensions"]["trend_momentum"],result["freshness"])
        con.close()

    def test_single_side_evidence_is_exploration_eligible(self):
        payload=self.payload()
        payload["topics"][0]["account_evidence_observed"]=False
        with patch("golden.retriever.pattern_support",return_value={"score":.8,"pattern_ids":["gp_external"],"breakdown":{}}):
            result=analyze.apply_unified_scores(payload,bump_version=False)
        self.assertTrue(result["topics"][0]["recommendation_eligible"])
        self.assertEqual(result["topics"][0]["recommendation_status"],"formal_external_exploration")
        self.assertEqual(result["topics"][0]["evidence_confidence"],.7)

    def test_no_real_evidence_is_not_eligible(self):
        payload=self.payload()
        payload["topics"][0]["account_evidence_observed"]=False
        payload["topics"][0]["stats"]={}
        with patch("golden.retriever.pattern_support",return_value={"score":0,"pattern_ids":[],"breakdown":{}}):
            result=analyze.apply_unified_scores(payload,bump_version=False)
        self.assertFalse(result["topics"][0]["recommendation_eligible"])
        self.assertEqual(result["topics"][0]["recommendation_status"],"insufficient_evidence")

    def test_current_batch_requires_real_human_click(self):
        cutoff="2026-08-14T10:00:00"
        candidates=[
          {"candidate_id":"old-human","source_reason":{"selections":[
            {"type":"human","selected_at":"2026-08-14T09:00:00"},
            {"type":"agent","selected_at":"2026-08-14T10:05:00"}]}},
          {"candidate_id":"new-human","source_reason":{"selections":[
            {"type":"human","selected_at":"2026-08-14T10:06:00"}]}},
        ]
        selected=topic_pipeline._human_confirmations_after(candidates,cutoff)
        self.assertEqual([x["candidate_id"] for x in selected],["new-human"])

    def test_pipeline_stage_state_round_trip_and_human_gate(self):
        with tempfile.TemporaryDirectory() as td:
            state_path=Path(td)/"topic_pipeline_stage.json"
            stage={"stage_id":"external_test","collected_at":"2026-08-14T10:00:00",
                   "status":"awaiting_human_golden","sample_rows":8}
            with patch.object(topic_pipeline,"STAGE_STATE",state_path):
                topic_pipeline._write_stage_state(stage)
                self.assertEqual(topic_pipeline._read_stage_state(),stage)
                denied=topic_pipeline.human_checkpoint([])
                self.assertFalse(denied["ok"])
                allowed=topic_pipeline.human_checkpoint([{"source_reason":{"selections":[
                    {"type":"human","selected_at":"2026-08-14T10:01:00"}]}}])
                self.assertTrue(allowed["ok"])
                topic_pipeline.mark_golden_analyzed(allowed["stage"],{"golden":1,"patterns":1})
                self.assertEqual(topic_pipeline._read_stage_state()["status"],"golden_analyzed")


if __name__ == "__main__":
    unittest.main()
