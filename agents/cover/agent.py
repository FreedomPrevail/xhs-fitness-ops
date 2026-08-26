from __future__ import annotations
from golden import retriever, repository
from golden.llm import call_json, is_configured, provider_name

def plan(title: str, cover_text: str, topic_label: str="", model_config: dict|None=None,
         usage_id: str="") -> dict:
    ev=retriever.retrieve(topic_label or title,3,3,purpose="cover")
    patterns=[{"pattern_id":p["pattern_id"],"segment":p["segment_key"],
               "effective_weight":p.get("effective_weight"),"updated_at":p.get("updated_at"),
               "cover_types":p.get("pattern",{}).get("cover_types",[]),
               "cover_layouts":p.get("pattern",{}).get("cover_layouts",[]),
               "cover_hooks":p.get("pattern",{}).get("cover_hooks",[])} for p in ev["patterns"]]
    samples=[]
    for s in ev["samples"]:
        cover=(s.get("features") or {}).get("cover",{})
        samples.append({
          "golden_id":s.get("golden_id", ""),"cover_type":cover.get("cover_type", "unknown"),
          "layout":cover.get("layout", "unknown"),"cover_hook":cover.get("cover_hook", ""),
          "text_length_estimate":cover.get("text_length_estimate",0),
          "learnable_elements":cover.get("learnable_elements",[]),
          "golden_score":s.get("golden_score",0),"transfer_score":s.get("transfer_score",.5),
          "revalidation_score":s.get("revalidation_score",1.0),
          "last_revalidated_at":s.get("last_revalidated_at", ""),
          "snapshot_count":len(repository.list_heat_snapshots(s.get("golden_id", ""))),
        })
    if is_configured(model_config, role="cover"):
        prompt=f'''你是 Cover Agent。根据最终标题、Golden Cover Pattern和Golden样本的封面特征，设计原创小红书封面 brief。只输出 JSON。不得复刻任何Golden封面。\n优先采用 effective_weight 高且 revalidation_score 显示仍有时效性的结构；低时效或久未复查样本只能作为弱证据。不得虚构看过未提供的图片。\n标题:{title}\n已有封面文字:{cover_text}\nGolden cover patterns:{patterns}\nGolden sample cover features:{samples}\n返回 {{"headline":"8-14字","concept":"","layout":"question|steps|editorial","visual_type":"","pattern_ids":{[p["pattern_id"] for p in ev["patterns"]]},"golden_ids":{[s["golden_id"] for s in ev["samples"]]},"reason":""}}'''
        try:
            out=call_json(prompt,model_config or {},schema="cover_agent_output.schema.json",role="cover")
            allowed_patterns=[p["pattern_id"] for p in ev["patterns"]]
            allowed_golden=[s["golden_id"] for s in ev["samples"]]
            out["pattern_ids"]=[x for x in out.get("pattern_ids",[]) if x in allowed_patterns] or allowed_patterns
            out["golden_ids"]=[x for x in out.get("golden_ids",[]) if x in allowed_golden] or allowed_golden
            out["llm_provider"]=provider_name(model_config,"cover")
            if usage_id: repository.add_usage_features(usage_id,{"cover_features":[{"golden_id":gid,"feature":"cover/visual_pattern"} for gid in out.get("golden_ids",[])]})
            return out
        except Exception:
            pass
    top=""
    for p in patterns:
        if p.get("cover_types"): top=p["cover_types"][0].get("value",""); break
    layout="question" if any(x in (cover_text or title) for x in ("?","？","为什么","怎么","别","错")) else ("steps" if any(ch.isdigit() for ch in (cover_text or title)) else "editorial")
    out={"headline":cover_text or title,"concept":top or "pattern-guided original cover","layout":layout,"visual_type":top or "unspecified","pattern_ids":[p["pattern_id"] for p in ev["patterns"]],"golden_ids":[s["golden_id"] for s in ev["samples"]],"reason":"Golden Pattern + Golden样本封面特征 + 当前标题"}
    if usage_id: repository.add_usage_features(usage_id,{"cover_features":[{"golden_id":gid,"feature":"cover/visual_pattern"} for gid in out.get("golden_ids",[])]})
    return out
