from __future__ import annotations
from collections import Counter,defaultdict
from datetime import date, datetime
from . import repository

# 滚动 Golden Pool:旧样本按时效×质量降权,权重衰减到阈值以下退出(存档保留,不删除)。
# 有效样本数不足说明该方向近期不再被采集/验证(不火了),整体按比例降权。
GOLDEN_HALF_LIFE_DAYS = 45.0
GOLDEN_EXIT_WEIGHT = 0.1
GOLDEN_MIN_SAMPLES = 3


def _age_days(sample: dict) -> int:
    ts = str(sample.get("created_at") or sample.get("published_at") or "").strip()[:10]
    try:
        return max(0, (date.today() - datetime.strptime(ts, "%Y-%m-%d").date()).days)
    except (ValueError, TypeError):
        return 0


def _get(d,*path,default=None):
    cur=d
    for p in path:
        if not isinstance(cur,dict): return default
        cur=cur.get(p)
    return default if cur in (None,"") else cur

def _top(counter: Counter,n=5):
    total=sum(counter.values()) or 1
    return [{"value":k,"weight":round(v/total,4)} for k,v in counter.most_common(n) if k]

def rebuild_patterns() -> list[dict]:
    samples=repository.list_samples(True); groups=defaultdict(list)
    for s in samples:
        f=s.get("features",{}); topic=_get(f,"topic","primary",default="未分类")
        intent=_get(f,"audience_intent","usage_context","intent_type",default="mixed")
        groups[f"{topic}|{intent}"].append(s)
    patterns=[]; exited=[]
    for seg,rows in groups.items():
        title=Counter(); hook=Counter(); structure=Counter(); audience=Counter(); browse=Counter(); search_scen=Counter(); query=Counter(); cover=Counter(); cta=Counter(); evidence=[]; totalw=0.0
        subtopic=Counter(); scenario=Counter(); fitness_goal=Counter(); audience_age=Counter(); audience_level=Counter(); pain_points=Counter(); knowledge=Counter()
        opening=Counter(); writing=Counter(); proof=Counter(); emotion=Counter(); cover_layout=Counter(); cover_hook=Counter()
        metric_sums=Counter(); metric_n=Counter(); transfer_sums=Counter(); transfer_n=Counter(); visual_cover_evidence=[]
        copy_examples=[]; content_evidence=[]; cover_evidence=[]; video_evidence=[]; media_counts=Counter()
        for s in rows:
            f=s.get("features",{}); w=max(.05,float(s.get("golden_score") or .5))
            # transfer_score 是内部发帖回流验证信号:与外部 golden_score 合并,内部验证越好的样本权重越高。
            w*=(.5+float(s.get("transfer_score") or .5))
            # revalidation_score 是外部热度持续性复查信号(持续增长提权、停滞降权),与 golden/transfer 并列,不回写字段。
            w*=float(s.get("revalidation_score") or 1.0)
            w*=0.5**(_age_days(s)/GOLDEN_HALF_LIFE_DAYS)  # 时效衰减
            if w<GOLDEN_EXIT_WEIGHT:
                exited.append(s["golden_id"]); continue
            transfer_dims=s.get("transfer_dimensions") if isinstance(s.get("transfer_dimensions"),dict) else {}
            for dim,val in transfer_dims.items():
                try: transfer_sums[str(dim)]+=float(val); transfer_n[str(dim)]+=1
                except (TypeError,ValueError): pass
            content_type=repository.normalize_content_type(s.get("content_type") or f.get("content_type"))
            if content_type=="video" and str(s.get("analysis_scope") or f.get("analysis_scope") or "").startswith("video_"):
                w*=.75  # 没有视听解析的视频仍能贡献Topic，但证据强度低于完整图文。
            totalw+=w
            media_counts[content_type]+=1
            content_eligible=(content_type=="image_text" or (content_type=="unknown" and bool(s.get("body"))))
            cover_value=_get(f,"cover","cover_type")
            cover_eligible=content_eligible and cover_value not in (None,"","unknown")
            def add(c,v):
                if v: c[str(v)]+=w
            # 标题、受众和搜索规律是通用Topic证据，图文和视频均可贡献。
            add(title,_get(f,"title_hook","title_pattern")); add(hook,_get(f,"title_hook","hook_type")); add(audience,_get(f,"audience_intent","audience_profile","goal"))
            add(emotion,_get(f,"title_hook","emotion")); add(subtopic,_get(f,"topic","subtopic")); add(scenario,_get(f,"topic","scenario")); add(fitness_goal,_get(f,"topic","fitness_goal"))
            add(audience_age,_get(f,"audience_intent","audience_profile","age_range")); add(audience_level,_get(f,"audience_intent","audience_profile","fitness_level")); add(knowledge,_get(f,"audience_intent","audience_profile","knowledge_level"))
            for x in _get(f,"audience_intent","audience_profile","pain_points",default=[]) or []: pain_points[str(x)]+=w
            cs=_get(f,"content","content_structure",default=[]) or []
            # 正文结构/CTA/封面只由图文或有正文的旧未知样本贡献。
            if content_eligible:
                if cs: structure[" → ".join(map(str,cs))]+=w
                add(cta,_get(f,"content","cta_type")); content_evidence.append(s["golden_id"])
                add(opening,_get(f,"content","opening_style")); add(writing,_get(f,"content","writing_style"))
                for x in _get(f,"content","proof_types",default=[]) or []: proof[str(x)]+=w
            if cover_eligible:
                add(cover,cover_value); cover_evidence.append(s["golden_id"])
                add(cover_layout,_get(f,"cover","layout")); add(cover_hook,_get(f,"cover","cover_hook"))
                if bool(_get(f,"cover","visual_observed",default=False)): visual_cover_evidence.append(s["golden_id"])
            if content_type=="video": video_evidence.append(s["golden_id"])
            for x in _get(f,"audience_intent","usage_context","likely_browse_periods",default=[]) or []: browse[str(x)]+=w
            for x in _get(f,"audience_intent","usage_context","likely_search_scenarios",default=[]) or []: search_scen[str(x)]+=w
            for x in _get(f,"audience_intent","search_behavior","likely_queries",default=[]) or []: query[str(x)]+=w
            evidence.append(s["golden_id"])
            for name,value in (("likes",s.get("likes")),("collects",s.get("collects")),("comments",s.get("comments"))):
                try: metric_sums[name]+=float(value or 0); metric_n[name]+=1
                except (TypeError,ValueError): pass
            if content_eligible and s.get("body") and len(copy_examples)<3: copy_examples.append({"golden_id":s["golden_id"],"title":s.get("title",""),"body_excerpt":s.get("body","")[:220]})
        # 样本少 = 该方向不火了:有效样本数不足 GOLDEN_MIN_SAMPLES 时按比例降权。
        heat_factor=min(1.0,len(evidence)/GOLDEN_MIN_SAMPLES)
        patterns.append({"segment_key":seg,"sample_count":len(evidence),"effective_weight":round((totalw/max(len(evidence),1))*heat_factor,4),"evidence":evidence,"pattern":{
          "title_patterns":_top(title),"hook_types":_top(hook),"title_emotions":_top(emotion),
          "content_structures":_top(structure),"opening_styles":_top(opening),"writing_styles":_top(writing),"proof_types":_top(proof),"cta_types":_top(cta),
          "topic_subtopics":_top(subtopic),"topic_scenarios":_top(scenario),"fitness_goals":_top(fitness_goal),
          "audience_goals":_top(audience),"audience_age_ranges":_top(audience_age),"audience_fitness_levels":_top(audience_level),"audience_pain_points":_top(pain_points),"audience_knowledge_levels":_top(knowledge),
          "likely_browse_periods":_top(browse),"likely_search_scenarios":_top(search_scen),"search_queries":_top(query),
          "cover_types":_top(cover),"cover_layouts":_top(cover_layout),"cover_hooks":_top(cover_hook),
          "platform_performance":{k:round(metric_sums[k]/max(1,metric_n[k]),2) for k in metric_sums},
          "transfer_dimensions":{k:round(transfer_sums[k]/max(1,transfer_n[k]),4) for k in transfer_sums},
          "copy_evidence":copy_examples,
          "media_counts":dict(media_counts),"topic_evidence":evidence,
          "image_text_content_evidence":list(dict.fromkeys(content_evidence)),
          "image_text_cover_evidence":list(dict.fromkeys(cover_evidence)),
          "visual_cover_evidence":list(dict.fromkeys(visual_cover_evidence)),
          "video_topic_evidence":list(dict.fromkeys(video_evidence)),
          "generation_media_policy":"Topic使用全部媒介；图文Content/Cover只使用图文或有正文的旧兼容样本。",
          "iteration_note":"权重 = golden_score × (0.5+transfer_score) × revalidation_score × 时效衰减(半衰期45天);transfer_score 是内部发帖回流验证信号;revalidation_score 是外部热度持续性复查信号(持续增长提权、停滞降权);有效样本<3按比例降权(样本少=不火了);权重<0.1退出。"
        }})
    if exited:
        repository.deactivate_samples(exited)
    return repository.replace_patterns(patterns)
