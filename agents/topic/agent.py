from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from analyze import analyze, heat, account_intelligence
from golden import retriever, repository as golden_repository
from golden.llm import call_json, is_configured, provider_name

ROOT=Path(__file__).resolve().parents[2]

def recommend(n: int=5) -> list[dict]:
    # 只读完整Pipeline最后一次原子提交的正式Topic Score；推荐刷新不重算。
    weights=analyze.load_weights()
    if not analyze.has_committed_score(weights):
        return []
    # 缺少内部或外部任一侧证据的选题只留在候选池，不进入正式推荐。
    topics=[t for t in weights.get("topics",[]) if t.get("recommendation_eligible")]; sdb=ROOT/"data"/"sample.db"
    con=sqlite3.connect(sdb) if sdb.exists() else None
    mdb=ROOT/"data"/"metrics.db"; account_report={"timing":{},"audience":{},"opportunities":[]}
    if mdb.exists():
        try:
            mcon=sqlite3.connect(mdb); account_report=account_intelligence.report(mcon,topics); mcon.close()
        except Exception:
            pass
    heats={}
    if con:
        for t in topics:
            kw=t.get("heat_keyword") or t["label"]; heats[t["id"]]=heat.platform_heat(kw,con)
        con.close()
    rows=[]
    for t in topics:
        h=heats.get(t["id"],{})
        rr=retriever.retrieve((t.get("heat_keyword") or t["label"]),k_samples=3,k_patterns=3,purpose="topic")
        primary_pattern=rr["patterns"][0] if rr["patterns"] else None
        ptn=(primary_pattern or {}).get("pattern",{})
        audience=((ptn.get("audience_goals") or [{}])[0].get("value") if ptn.get("audience_goals") else "")
        seed=t.get("topic_brief_seed") if isinstance(t.get("topic_brief_seed"),dict) else {}
        external_periods=(ptn.get("likely_browse_periods") or [])[:3]
        account_periods=(account_report.get("timing") or {}).get("periods",[])[:3]
        score=float(t.get("final_score",t.get("weight",0)))
        pattern_ids=t.get("pattern_ids") or [p["pattern_id"] for p in rr["patterns"]]
        rows.append({
          "id":t["id"],"label":t["label"],"category":t["category"],"weight":round(score,3),"score":round(score,3),
          "account_weight":t.get("account_weight",t.get("base_weight",1.0)),"account_score":t.get("account_score",.5),"platform_score":t.get("platform_score",.5),
          "account_dimensions":t.get("account_dimensions",{}),"account_data_coverage":t.get("account_data_coverage",0),
          "account_confidence":t.get("account_confidence",0),"account_missing_dimensions":t.get("account_missing_dimensions",[]),
          "platform_dimensions":t.get("platform_dimensions",{}),"platform_data_coverage":t.get("platform_data_coverage",0),
          "platform_heat":t.get("stats",{}).get("platform_heat"),"live_heat":h.get("heat",0),"live_n":h.get("n",0),"live_freshness":h.get("freshness",0),"live_phrases":h.get("common_phrases",[]),
          "keyword":t.get("heat_keyword") or t["label"],
          "golden_support":t.get("golden_score",0),"novelty_score":t.get("novelty_score",0),
          "score_breakdown":t.get("score_breakdown",{}),"source":t.get("source","curated"),
          "trend_evidence":t.get("trend_evidence",[]),"pattern_ids":pattern_ids,"golden_ids":[s["golden_id"] for s in rr["samples"]],
          "topic_brief":{"topic":t["label"],"angle":seed.get("angle") or ((ptn.get("title_patterns") or [{}])[0].get("value") if ptn.get("title_patterns") else ""),"audience":seed.get("audience") or {"goal":audience},"intent":seed.get("intent") or (primary_pattern.get("segment_key","").split("|")[-1] if primary_pattern else "mixed"),"keywords":seed.get("keywords") or [t.get("heat_keyword") or t["label"]],"pattern_ids":pattern_ids,"account_evidence":t.get("account_evidence",[]),"account_dimensions":t.get("account_dimensions",{}),"account_data_center":(account_report.get("data_center") or {}).get("account_metrics",{}),"timing_hypothesis":{"account_observed_or_estimated":account_periods,"account_source":(account_report.get("timing") or {}).get("source","missing"),"account_confidence":(account_report.get("timing") or {}).get("confidence",0),"golden_inferred":external_periods},"score":round(score,3),"reason":f"统一权重=账号35%+平台30%+Golden30%+新颖度5%"}
        })
    return sorted(rows,key=lambda x:x["score"],reverse=True)[:n]


def _revalidation_evidence(golden_ids: list[str]) -> list[dict]:
    evidence=[]
    for golden_id in golden_ids:
        sample=golden_repository.get_sample(golden_id)
        if not sample: continue
        snapshots=golden_repository.list_heat_snapshots(golden_id)
        evidence.append({
          "golden_id":golden_id,"title":sample.get("title", "")[:80],
          "golden_score":round(float(sample.get("golden_score") or 0),4),
          "transfer_score":round(float(sample.get("transfer_score") or .5),4),
          "revalidation_score":round(float(sample.get("revalidation_score") or 1.0),4),
          "last_revalidated_at":sample.get("last_revalidated_at") or "",
          "snapshot_count":len(snapshots),
        })
    return evidence


def decide_daily(model_config: dict | None=None, *, n: int=8, persona: dict | None=None,
                 manual_candidates: list[dict] | None=None, recent_decisions: list[dict] | None=None,
                 materials: list[dict] | None=None) -> dict:
    """Use an LLM to choose among canonical scored topics without inventing a new score."""
    candidates=recommend(n)
    if not candidates:
        return {"ok":False,"error":"尚无已提交的正式 Topic Score，无法做每日决策"}
    packet=[]
    for item in candidates:
        current=retriever.retrieve(item.get("keyword") or item.get("label") or "",k_samples=5,k_patterns=3,purpose="topic")
        current_patterns=[{"pattern_id":p.get("pattern_id"),"segment_key":p.get("segment_key"),
                           "effective_weight":p.get("effective_weight"),"updated_at":p.get("updated_at"),
                           "topic_support_score":p.get("topic_support_score"),
                           "iteration_note":(p.get("pattern") or {}).get("iteration_note","")}
                          for p in current.get("patterns",[])]
        current_golden_ids=[s.get("golden_id") for s in current.get("samples",[]) if s.get("golden_id")]
        packet.append({
          "topic_id":item["id"],"label":item["label"],"category":item["category"],
          "canonical_score":item["score"],"score_breakdown":item.get("score_breakdown",{}),
          "topic_brief":item.get("topic_brief",{}),"pattern_ids":item.get("pattern_ids",[]),
          "golden_ids":current_golden_ids or item.get("golden_ids",[]),
          "current_patterns":current_patterns,
          "revalidation_evidence":_revalidation_evidence(current_golden_ids or item.get("golden_ids",[])),
          "cached_trend":{"heat":item.get("live_heat",0),"sample_n":item.get("live_n",0),
                          "freshness":item.get("live_freshness",0),"phrases":item.get("live_phrases",[])},
        })
    profile=(persona or {}).get("account",{})
    history=[]
    for row in (recent_decisions or [])[:14]:
        decision=row.get("decision") if isinstance(row.get("decision"),dict) else row
        history.append({"date":row.get("decision_date") or row.get("created_at"),
                        "topic_id":decision.get("selected_topic_id"),"angle":decision.get("angle","")})
    material_pack=[{"material_id":x.get("material_id"),"kind":x.get("kind"),"title":x.get("title"),
                    "notes":x.get("notes"),"tags":x.get("tags",[]),"rights_status":x.get("rights_status")}
                   for x in (materials or [])[:30]]
    manual=[{"candidate_id":x.get("candidate_id"),"title":x.get("title"),"keyword":x.get("keyword"),
             "published_at":x.get("published_at"),"likes":x.get("likes",0),
             "collects":x.get("collects",0),"comments":x.get("comments",0),
             "source_type":x.get("source_type","manual_sync")}
            for x in (manual_candidates or [])[:30]]
    fallback={"ok":True,"llm_used":False,"llm_provider":"none","selected_topic_id":candidates[0]["id"],
              "decision":"adopt","reason":"未配置 LLM，按正式 Topic Score 首位降级选择",
              "angle":candidates[0].get("topic_brief",{}).get("angle", ""),
              "audience":candidates[0].get("topic_brief",{}).get("audience",{}),
              "intent":candidates[0].get("topic_brief",{}).get("intent","mixed"),
              "keywords":candidates[0].get("topic_brief",{}).get("keywords",[]),
              "evidence":{"pattern_ids":candidates[0].get("pattern_ids",[]),
                          "golden_ids":candidates[0].get("golden_ids",[]),"manual_candidate_ids":[]},
              "data_freshness":"cached","risks":["LLM 未配置"],"material_ids":[]}
    if not is_configured(model_config, role="topic"):
        return fallback
    prompt=f'''你是这个账号的每日 Topic Agent。只能在给定 topic_id 中选择，不得重算或覆盖 canonical_score。
结合账号定位、Golden Pattern、每个 Golden Sample 独立的 golden/transfer/revalidation 信号、人工同步候选、近期避免重复记录和本人素材，判断今天采用、暂缓还是淘汰哪个选题。
revalidation_score 只表示人工快照计算的时效乘数；快照不足时必须降低置信度，不得猜热点。不得把缓存数据称为实时数据。
只输出 JSON：
{{"selected_topic_id":"","decision":"adopt|hold|skip","reason":"","angle":"","audience":{{}},"intent":"","keywords":[],"evidence":{{"pattern_ids":[],"golden_ids":[],"manual_candidate_ids":[]}},"data_freshness":"fresh|mixed|stale|unknown","risks":[],"material_ids":[]}}

账号定位：{json.dumps(profile,ensure_ascii=False)}
正式候选与证据：{json.dumps(packet,ensure_ascii=False)}
人工同步候选：{json.dumps(manual,ensure_ascii=False)}
最近决策：{json.dumps(history,ensure_ascii=False)}
本人素材：{json.dumps(material_pack,ensure_ascii=False)}'''
    try:
        out=call_json(prompt,model_config or {},schema="daily_topic_decision.schema.json",role="topic")
    except Exception as exc:
        return {**fallback,"reason":"LLM 决策失败，按正式分数降级："+str(exc)[:160],"risks":["LLM 调用失败"]}
    allowed={item["id"]:item for item in candidates}
    packet_by_id={item["topic_id"]:item for item in packet}
    topic_id=str(out.get("selected_topic_id") or "")
    if topic_id not in allowed:
        return {**fallback,"reason":"LLM 返回了候选集之外的选题，已按正式分数降级","risks":["LLM 选题 ID 无效"]}
    chosen=allowed[topic_id]; current_packet=packet_by_id[topic_id]
    allowed_patterns=set(chosen.get("pattern_ids",[]))|{x.get("pattern_id") for x in current_packet.get("current_patterns",[]) if x.get("pattern_id")}
    allowed_golden=set(current_packet.get("golden_ids",[]) or chosen.get("golden_ids",[]))
    evidence=out.get("evidence") if isinstance(out.get("evidence"),dict) else {}
    evidence["pattern_ids"]=[x for x in evidence.get("pattern_ids",[]) if x in allowed_patterns] or list(allowed_patterns)
    evidence["golden_ids"]=[x for x in evidence.get("golden_ids",[]) if x in allowed_golden] or list(allowed_golden)
    manual_ids={str(x.get("candidate_id")) for x in manual if x.get("candidate_id")}
    evidence["manual_candidate_ids"]=[x for x in evidence.get("manual_candidate_ids",[]) if str(x) in manual_ids]
    return {"ok":True,"llm_used":True,"llm_provider":provider_name(model_config,"topic"),"selected_topic_id":topic_id,
            "decision":out.get("decision") if out.get("decision") in ("adopt","hold","skip") else "hold",
            "reason":str(out.get("reason") or "")[:800],"angle":str(out.get("angle") or "")[:300],
            "audience":out.get("audience") if isinstance(out.get("audience"),dict) else {},
            "intent":str(out.get("intent") or "mixed"),
            "keywords":[str(x) for x in (out.get("keywords") or [])[:8]],"evidence":evidence,
            "data_freshness":out.get("data_freshness") if out.get("data_freshness") in ("fresh","mixed","stale","unknown") else "unknown",
            "risks":[str(x) for x in (out.get("risks") or [])[:8]],
            "material_ids":[str(x) for x in (out.get("material_ids") or [])[:10]],
            "canonical_score":chosen["score"],"topic":chosen}
