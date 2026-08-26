from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from agents.content import agent as content_agent
from agents import outline_agent, visual_director_agent
from agents.reviewer import agent as reviewer_agent
from content.carousel import asset_planner, render_post
from personal_ops import repository


class PersonalVoiceMemoryTest(unittest.TestCase):
    def test_owned_voice_sample_is_persistent_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(repository, "DB", Path(tmp) / "memory.db"):
            repository.upsert_material({
                "material_id": "voice_one", "kind": "writing_sample", "title": "本人文章",
                "content_text": "先给判断，再用数字和对比解释。" * 200,
                "purpose": "voice_and_argumentation_style_only", "rights_status": "owned",
            })
            repository.upsert_material({
                "material_id": "blocked", "kind": "writing_sample", "title": "未授权",
                "content_text": "不能使用", "rights_status": "unknown",
            })
            memory = repository.writing_memory(limit=6, excerpt_chars=240)
            self.assertEqual([row["material_id"] for row in memory], ["voice_one"])
            self.assertLessEqual(len(memory[0]["excerpt"]), 240)

    def test_content_prompt_marks_samples_voice_only(self):
        material = {"material_id":"voice_one","kind":"writing_sample","title":"本人文章",
                    "content_text":"先给判断，再解释证据，最后说明限制。",
                    "purpose":"voice_and_argumentation_style_only","rights_status":"owned","tags":[]}
        prompt = content_agent.build_prompt(
            {"id":"fitness","label":"新手力量训练","category":"fitness_value"},
            {"account":{"positioning":"健身技巧与减脂指南","voice_profile":{}}},
            {"compliance_redlines":{}}, [], {"samples":[],"patterns":[]}, [material])
        self.assertIn("个人写作样本（voice-only）", prompt)
        self.assertIn("不得把样本中的社会议题", prompt)
        self.assertIn("先给判断，再解释证据", prompt)

    def test_page_type_changes_visual_prompt(self):
        outline={"pages":[
            {"page_no":1,"type":"cover","title":"减脂指南","bullets":[],"needs_visual":True,"visual_role":"hero","scene":"训练工具"},
            {"page_no":2,"type":"comparison","title":"两种做法","bullets":["A","B"],"needs_visual":True,"visual_role":"supporting","scene":"饮食选择"},
        ]}
        visual=visual_director_agent.build(outline,{"account":{"positioning":"健身与减脂","voice_profile":{}}})
        assets=asset_planner.build("post",visual,[])
        prompt=assets["asset_tasks"][0]["prompt"]
        self.assertIn("balanced split composition",prompt)
        self.assertEqual(visual["pages"][1]["page_type"],"comparison")

    def test_reviewer_flags_academic_density(self):
        content={"title":"新手减脂怎么做","cover_text":"新手减脂指南",
                 "body":"首先，这是一条很长的解释。其次，需要具体行动。再次，要看身体反馈。再者，不要追求极端。综上所述，要循序渐进。",
                 "evidence":{"golden_ids":[],"pattern_ids":[]}}
        review=reviewer_agent.review(
            content,{"label":"减脂","category":"fitness_value"},
            {"compliance_redlines":{"forbidden_phrases":[]}},personal_materials=[])
        self.assertTrue(any("学术连接词" in warning for warning in review["warnings"]))

    def test_dense_outline_uses_multiple_infographic_templates_and_renders(self):
        units=[{"unit_id":f"ku{index:02d}","kind":"step" if index<9 else "check",
                "label":f"知识点{index}","detail":f"这是第{index}个可执行说明，需要保留条件和安全边界",
                "condition":"按个人基础调整","action":"记录身体反馈","safety_note":"明显不适时停止",
                "icon_hint":"训练图标","basis":"general_knowledge"} for index in range(1,31)]
        content={"title":"新手健身房完整路线","cover_text":"第一次去也不慌","body":"完整训练指南",
                 "knowledge_units":units,"evidence":{"pattern_ids":[],"golden_ids":[]}}
        outline=outline_agent.build({"id":"demo","label":"健身新手"},content,{})
        self.assertEqual(outline["page_count"],6)
        self.assertGreaterEqual(sum(page.get("info_unit_count",0) for page in outline["pages"]),30)
        visual=visual_director_agent.build(outline,{"account":{"positioning":"健身与减脂","voice_profile":{}}})
        self.assertGreaterEqual(len({page["template_type"] for page in visual["pages"]}),5)
        plan=asset_planner.build("dense_demo",visual,[])
        with tempfile.TemporaryDirectory() as tmp:
            paths=render_post.render(outline,visual,plan,tmp)
            self.assertEqual(len(paths),6)
            with Image.open(paths[2]) as rendered:
                self.assertEqual(rendered.size,(1080,1440))


if __name__ == "__main__":
    unittest.main()
