"""Daily workflow for one-person Xiaohongshu content operations."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agents.content import agent as content_agent
from agents.reviewer import agent as reviewer_agent
from agents.topic import agent as topic_agent
from analyze import analyze
from content import generate as legacy_generate
from content.carousel import generate_carousel
from golden import repository as golden_repository
from golden import llm as llm_gateway
from personal_ops import repository

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "daily_runs"


def ensure_current_topic_score() -> dict:
    """Refresh the canonical score from current account/local evidence before a decision."""
    weights=analyze.load_weights()
    metrics_db=ROOT/"data"/"metrics.db"; sample_db=ROOT/"data"/"sample.db"
    account_evidence=0; sample_rows=0
    if metrics_db.exists():
        con=sqlite3.connect(metrics_db)
        try:
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("note_metrics","account_stats","account_audience_snapshots"):
                if table in tables: account_evidence+=int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            weights=analyze.apply_account_intelligence(weights,con)
        finally: con.close()
    if sample_db.exists():
        con=sqlite3.connect(sample_db)
        try: sample_rows=int(con.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
        finally: con.close()
    overview=golden_service.overview(); counts=overview.get("counts") or {}
    evidence={"sample_rows":sample_rows,"golden_candidates":counts.get("candidates",0),
              "golden_samples":counts.get("golden",0),"golden_patterns":counts.get("patterns",0),
              "account_evidence":account_evidence,"source":"manual_connected_current_and_cached",
              "operation_mode":"manual_connected"}
    pipeline_id="daily_"+datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        committed=analyze.commit_unified_scores(weights,pipeline_id,evidence)
    except Exception as exc:
        return {"ok":False,"created":False,"error":str(exc),"evidence":evidence}
    return {"ok":True,"created":True,"refreshed":True,"version":committed.get("version"),
            "pipeline_id":pipeline_id,"evidence":evidence}


def daily_decision(model_config: dict | None=None) -> dict:
    score_state=ensure_current_topic_score()
    if not score_state.get("ok"):
        return {"ok":False,"error":"无法从当前证据建立正式 Topic Score："+score_state.get("error", ""),
                "score_state":score_state}
    persona=legacy_generate.load("persona.yaml")
    candidates=golden_repository.list_candidates(60)
    result=topic_agent.decide_daily(
        model_config or {},persona=persona,manual_candidates=candidates,
        recent_decisions=repository.recent_decisions(14),materials=repository.list_materials(50))
    if result.get("ok"):
        result["run_id"]=repository.save_decision(result)
        result["score_state"]=score_state
    return result


def run_daily(payload: dict | None=None) -> dict:
    """Run LLM decision -> content -> carousel from the current analysis stores."""
    payload=payload or {}
    model_config=dict(payload.get("model_config") or {})
    missing=[role for role in ("topic","content") if not llm_gateway.is_configured(model_config,role=role)]
    if missing:
        return {"ok":False,"error":"这些 Agent 尚未配置可用模型："+",".join(missing)+"；请选择 Codex CLI，或配置完整的 Base URL、API Key 和模型名"}
    decision=daily_decision(model_config)
    if not decision.get("ok"):
        return {"ok":False,"stage":"topic_decision","decision":decision}
    if decision.get("decision") != "adopt":
        return {"ok":True,"stage":"decision_only","decision":decision}
    weights=analyze.load_weights()
    topic=next((t for t in weights.get("topics",[]) if t.get("id")==decision["selected_topic_id"]),None)
    if not topic:
        return {"ok":False,"stage":"topic_lookup","error":"每日决策选题已不在正式权重表中"}
    topic={**topic,"topic_brief":{**(topic.get("topic_brief_seed") or {}),
          "topic":topic.get("label", ""),"angle":decision.get("angle", ""),
          "audience":decision.get("audience",{}),"intent":decision.get("intent","mixed"),
          "keywords":decision.get("keywords",[]),"pattern_ids":decision.get("evidence",{}).get("pattern_ids",[])}}
    persona=legacy_generate.load("persona.yaml"); insurance=legacy_generate.load("insurance.yaml")
    personal_materials=repository.list_materials(50)
    content=content_agent.generate(
        topic,[],model_config,persona,insurance,personal_materials,
        selected_material_ids=decision.get("material_ids",[]))
    content_review=reviewer_agent.review(
        content,topic,insurance,persona=persona,personal_materials=personal_materials)
    image_options=dict(model_config.get("image_generation") or {})
    image_options["enabled"]=bool(payload.get("generate_images",False))
    if payload.get("max_image_assets") not in (None,""):
        image_options["max_assets"]=max(3,int(payload["max_image_assets"]))
    model_config["image_generation"]=image_options
    carousel=generate_carousel.generate(topic,content,persona,model_config,personal_materials)
    run_id=decision.get("run_id") or "daily_"+datetime.now().strftime("%Y%m%d_%H%M%S")
    RUNS.mkdir(parents=True,exist_ok=True)
    result={"ok":True,"stage":"package_ready","run_id":run_id,"decision":decision,
            "content":content,"content_review":content_review,"carousel":carousel,
            "llm_provider":llm_gateway.provider_name(model_config),
            "llm_providers":{role:llm_gateway.provider_name(model_config,role)
                             for role in ("topic","golden","content","outline","cover","image")},
            "manual_publish_required":True}
    (RUNS/f"{run_id}.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result
