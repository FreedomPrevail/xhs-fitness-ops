from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from golden import llm


class LlmGatewayTest(unittest.TestCase):
    def test_provider_readiness_keeps_http_and_codex(self):
        self.assertTrue(llm.is_configured({"provider":"codex_cli"}))
        self.assertTrue(llm.is_configured({"provider":"http","base_url":"https://example.test/v1",
                                           "api_key":"secret","model":"model"}))
        self.assertFalse(llm.is_configured({"provider":"http","base_url":"https://example.test/v1"}))

    def test_role_routing_keeps_deepseek_for_content_and_codex_for_outline(self):
        cfg={"provider":"http","base_url":"https://example.test/v1","api_key":"secret","model":"deepseek-text",
             "providers":{"http":{"base_url":"https://example.test/v1","api_key":"secret","model":"deepseek-text"},
                          "codex_cli":{"model":"codex-visual","codex_cli_path":"codex"}},
             "agent_providers":{"topic":"http","content":"http","outline":"codex_cli","cover":"codex_cli"}}
        self.assertEqual(llm.provider_name(cfg,"content"),"http")
        self.assertEqual(llm.agent_config(cfg,"content")["model"],"deepseek-text")
        self.assertEqual(llm.provider_name(cfg,"outline"),"codex_cli")
        self.assertEqual(llm.agent_config(cfg,"outline")["model"],"codex-visual")

    def test_codex_exec_uses_schema_read_only_and_role_depth(self):
        captured={}

        def fake_run(args, **kwargs):
            captured["args"]=list(args); captured["input"]=kwargs.get("input")
            result_path=Path(args[args.index("--output-last-message")+1])
            result_path.write_text(json.dumps({"result_json":json.dumps({"selected_topic_id":"topic_a","decision":"hold"})}),encoding="utf-8")
            return SimpleNamespace(returncode=0,stdout="",stderr="")

        cfg={"provider":"codex_cli","agent_reasoning":{"topic":"high"}}
        with patch.object(llm,"_cli_available",return_value=True), patch.object(llm.subprocess,"run",side_effect=fake_run):
            result=llm.call_json("choose",cfg,schema={"type":"object"},role="topic")
        self.assertEqual(result["selected_topic_id"],"topic_a")
        self.assertIn("choose",captured["input"])
        self.assertIn("result_json",captured["input"])
        self.assertIn("--output-schema",captured["args"])
        self.assertIn("--ephemeral",captured["args"])
        self.assertEqual(captured["args"][captured["args"].index("--sandbox")+1],"read-only")
        self.assertIn('model_reasoning_effort="high"',captured["args"])
        self.assertFalse(any("dangerously" in item or item=="--yolo" for item in captured["args"]))

    def test_local_schema_validation_rejects_missing_required_field(self):
        with self.assertRaises(RuntimeError):
            llm._validate_value({"decision":"hold"},{"type":"object","required":["selected_topic_id"],
                                                         "properties":{"selected_topic_id":{"type":"string"}}})

    def test_http_backend_remains_available(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self):
                return json.dumps({"choices":[{"message":{"content":"{\"ok\":true}"}}]}).encode()
        cfg={"provider":"http","base_url":"https://example.test/v1","api_key":"secret","model":"model"}
        with patch.object(llm.urllib.request,"urlopen",return_value=Response()):
            self.assertEqual(llm.call_json("test",cfg),{"ok":True})


if __name__ == "__main__":
    unittest.main()
