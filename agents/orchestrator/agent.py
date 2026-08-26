from __future__ import annotations
from analyze import analyze
from content import generate as legacy_generate
from agents.topic import agent as topic_agent
from agents.content import agent as content_agent
from agents.cover import agent as cover_agent
from agents.reviewer import agent as reviewer_agent


def run(topic_id: str="", refs: list[dict]|None=None, model_config: dict|None=None) -> dict:
    refs=refs or []; model_config=model_config or {}
    recommendations=topic_agent.recommend(10)
    if topic_id:
        brief=next((r for r in recommendations if r["id"]==topic_id),None)
    else:
        brief=recommendations[0] if recommendations else None
    weights=analyze.load_weights()
    topic=next((t for t in weights.get("topics",[]) if t.get("id")==((brief or {}).get("id") or topic_id)),None)
    if not topic: return {"ok":False,"error":"Orchestrator 未找到可用选题"}
    persona=legacy_generate.load("persona.yaml"); insurance=legacy_generate.load("insurance.yaml")
    content=content_agent.generate(topic,refs,model_config,persona,insurance)
    cover=cover_agent.plan(content.get("title",""),content.get("cover_text",""),topic.get("label",""),model_config,content.get("golden_usage_id",""))
    review=reviewer_agent.review(content,topic,insurance)
    return {"ok":True,"topic":brief or {"id":topic["id"],"label":topic["label"]},"content":content,"cover_plan":cover,"review":review,"ready_for_draft":review.get("status")=="PASS"}
