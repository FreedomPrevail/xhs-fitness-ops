from __future__ import annotations
import re
from . import repository


def _grams(text: str) -> set[str]:
    s=re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]","",text or "").lower()
    out=set()
    for n in (2,3):
        for i in range(max(0,len(s)-n+1)): out.add(s[i:i+n])
    return out


def _sim(a: str,b: str) -> float:
    A=_grams(a); B=_grams(b)
    if not A or not B: return 0.0
    return len(A&B)/max(1,min(len(A),len(B)))


def _pattern_text(pattern: dict) -> str:
    """用于选题匹配的Pattern语义，不读取或改写Golden原文。"""
    p=pattern.get("pattern",{}) if isinstance(pattern.get("pattern"),dict) else {}
    values=[]
    for key in ("topic_subtopics","topic_scenarios","fitness_goals","audience_goals",
                "audience_pain_points","likely_search_scenarios","search_queries","title_patterns",
                "hook_types","opening_styles","content_structures","proof_types","cover_types",
                "cover_layouts","cover_hooks"):
        for item in p.get(key,[]) or []:
            if isinstance(item,dict) and item.get("value"):
                values.append(str(item["value"]))
    return " ".join([str(pattern.get("segment_key") or ""),*values])


def _values_text(pattern: dict, keys: tuple[str,...]) -> str:
    p=pattern.get("pattern",{}) if isinstance(pattern.get("pattern"),dict) else {}
    values=[]
    for key in keys:
        for item in p.get(key,[]) or []:
            if isinstance(item,dict) and item.get("value"): values.append(str(item["value"]))
    return " ".join(values)


def pattern_support(query: str, limit: int=3, cited_pattern_ids: list[str] | None=None) -> dict:
    """给统一选题权重提供0~1 Golden Pattern支持度与证据ID。

    只有与选题存在语义重叠的Pattern才计分；Pattern自身权重不能让无关
    选题凭空获得Golden支持。
    """
    cited={str(x) for x in (cited_pattern_ids or []) if str(x).strip()}
    matches=[]; all_patterns=repository.list_patterns()
    perf_raw=[]
    for p in all_patterns:
        perf=(p.get("pattern") or {}).get("platform_performance",{})
        perf_raw.append(float(perf.get("likes",0))+2*float(perf.get("collects",0))+3*float(perf.get("comments",0)))
    max_perf=max(perf_raw or [0])
    for p,raw_perf in zip(all_patterns,perf_raw):
        groups={
          "content":" ".join([str(p.get("segment_key") or ""),_values_text(p,("topic_subtopics","topic_scenarios","fitness_goals","content_structures","proof_types"))]),
          "audience":_values_text(p,("audience_goals","audience_pain_points","audience_fitness_levels","likely_search_scenarios","search_queries")),
          "copy":_values_text(p,("title_patterns","hook_types","title_emotions","opening_styles","writing_styles","cta_types")),
          "cover":_values_text(p,("cover_types","cover_layouts","cover_hooks")),
        }
        dims={k:_sim(query,text) if text.strip() else None for k,text in groups.items()}
        rel=_sim(query,_pattern_text(p))
        # Discovery Agent只能从传给它的真实Pattern ID中引用；命中的引用视为
        # 已建立选题→Pattern关系，但仍由Pattern质量决定最终支持度。
        if p.get("pattern_id") in cited:
            rel=max(rel,.6)
            dims["content"]=max(float(dims.get("content") or 0),.6)
            dims["audience"]=max(float(dims.get("audience") or 0),.45)
        if rel <= 0:
            continue
        weights={"content":.35,"audience":.30,"copy":.20,"cover":.15}
        available={k:v for k,v in dims.items() if v is not None}
        denom=sum(weights[k] for k in available) or 1
        feature_fit=sum(weights[k]*v for k,v in available.items())/denom
        feature_fit=max(feature_fit,rel*.75)
        pattern_quality=min(1.0,max(0.0,float(p.get("effective_weight") or 0)))
        platform_quality=(raw_perf/max_perf) if max_perf>0 else .5
        transfer=(p.get("pattern") or {}).get("transfer_dimensions",{})
        transfer_values=[float(v) for v in transfer.values() if isinstance(v,(int,float))]
        transfer_quality=sum(transfer_values)/len(transfer_values) if transfer_values else .5
        # transfer_quality 是内部发帖回流的验证信号,与 pattern 质量、平台热度共同构成 evidence_quality。
        evidence_quality=min(1.0,.55+.15*pattern_quality+.15*platform_quality+.15*transfer_quality)
        score=feature_fit*evidence_quality
        p={**p,"golden_dimensions":{k:round(float(v or 0),4) for k,v in dims.items()},
           "feature_fit":round(feature_fit,4),"platform_quality":round(platform_quality,4),
           "transfer_quality":round(transfer_quality,4),"evidence_quality":round(evidence_quality,4)}
        matches.append((score,p))
    chosen=sorted(matches,key=lambda x:x[0],reverse=True)[:max(0,int(limit))]
    if not chosen:
        return {"score":0.0,"pattern_ids":[],"patterns":[]}
    # 取最匹配Pattern为主，其他证据提供少量增益，避免Pattern数量多就虚高。
    support=min(1.0,chosen[0][0]+sum(x[0] for x in chosen[1:])*.15)
    return {
      "score":round(support,4),
      "pattern_ids":[p["pattern_id"] for _,p in chosen],
      "patterns":[{**p,"topic_support_score":round(sc,4),
                    "explicitly_cited":p.get("pattern_id") in cited} for sc,p in chosen],
      "breakdown":chosen[0][1].get("golden_dimensions",{}) if chosen else {},
    }


def content_sample_eligible(sample: dict) -> bool:
    content_type=repository.normalize_content_type(sample.get("content_type") or (sample.get("features") or {}).get("content_type"))
    return content_type=="image_text" or (content_type=="unknown" and bool((sample.get("body") or "").strip()))


def cover_sample_eligible(sample: dict) -> bool:
    if not content_sample_eligible(sample): return False
    cover=(sample.get("features") or {}).get("cover",{})
    if not isinstance(cover,dict): return False
    return bool((cover.get("cover_type") not in (None,"","unknown"))
                or (cover.get("layout") not in (None,"","unknown"))
                or cover.get("cover_hook") or cover.get("learnable_elements"))


def retrieve(query: str, k_samples: int=5, k_patterns: int=3, purpose: str="topic") -> dict:
    """按用途召回证据：Topic可看全部媒介，图文Content/Cover只看合格同形态证据。"""
    purpose=purpose if purpose in ("topic","content","cover") else "topic"
    samples=[]
    for s in repository.list_samples(True):
        if purpose=="content" and not content_sample_eligible(s): continue
        if purpose=="cover" and not cover_sample_eligible(s): continue
        f=s.get("features",{})
        text=" ".join([s.get("title", ""),s.get("keyword", ""),str(f.get("topic",{}).get("primary", "")),str(f.get("topic",{}).get("subtopic", "")),str(f.get("analysis_summary",{}).get("why_selected", ""))])
        rel=_sim(query,text)
        revalidation=max(.3,min(1.8,float(s.get("revalidation_score") or 1.0)))/1.8
        score=.45*rel+.22*float(s.get("golden_score") or 0)+.18*float(s.get("transfer_score") or .5)+.15*revalidation
        samples.append((score,s))
    patterns=[]
    for p in repository.list_patterns():
        body=p.get("pattern",{}) if isinstance(p.get("pattern"),dict) else {}
        if purpose=="content" and not (body.get("image_text_content_evidence") or body.get("content_structures")): continue
        if purpose=="cover" and not body.get("cover_types"): continue
        rel=_sim(query,p.get("segment_key", ""))
        score=.65*rel+.35*min(1.0,float(p.get("effective_weight") or 0))
        patterns.append((score,p))
    return {
      "samples":[{**s,"retrieval_score":round(sc,4)} for sc,s in sorted(samples,key=lambda x:x[0],reverse=True)[:k_samples] if sc>0],
      "patterns":[{**p,"retrieval_score":round(sc,4)} for sc,p in sorted(patterns,key=lambda x:x[0],reverse=True)[:k_patterns] if sc>0],
      "purpose":purpose,
    }
