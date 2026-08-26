from __future__ import annotations
from . import repository

def _clamp(v):
    try:return max(0.0,min(1.0,float(v)))
    except:return 0.5

def evaluate(c: dict, f: dict) -> tuple[str,list,float]:
    reasons=[]; hard=[]
    if not (c.get("title") or "").strip(): hard.append("缺少标题")
    domain=_clamp(f.get("topic",{}).get("domain_relevance",.7))
    learn=bool(f.get("transferability",{}).get("learnable_pattern",True))
    if domain<.25: hard.append("与健身领域相关性过低")
    if not learn: reasons.append("可学习模式较弱")
    content_type=repository.normalize_content_type(c.get("content_type"))
    if content_type=="video":
        reasons.append("WARN: 尚无视频文件/关键帧/ASR，仅完成弱分析；只参与Topic规律，不参与图文Content/Cover规律")
    elif not (c.get("body") or "").strip():
        reasons.append("WARN: 尚未抓到完整正文，Pattern 权重应低于正文完整样本")
    if float(f.get("transferability",{}).get("celebrity_dependency",0) or 0)>.8: reasons.append("WARN: 强明星/IP依赖")
    status="FAIL" if hard else ("WARN" if reasons else "PASS")
    reasons=hard+reasons
    q=_clamp(f.get("transferability",{}).get("content_quality",.5)); rep=_clamp(f.get("transferability",{}).get("replicability",.5)); nov=_clamp(f.get("transferability",{}).get("novelty",.5)); sea=_clamp(f.get("audience_intent",{}).get("search_behavior",{}).get("searchability_score",.5)); cand=_clamp(c.get("candidate_score",.5)); human=1.0 if "human" in str(c.get("source_type") or "") else .5
    score=.25*cand+.20*q+.20*rep+.10*domain+.10*sea+.10*nov+.05*human
    if content_type=="video": score*=.9 if c.get("body") else .85
    elif not c.get("body"): score*=.9
    return status,reasons,round(score,4)

def admit_featured(limit: int=100) -> dict:
    admitted=[]; skipped=[]
    for c in repository.pending_candidates(limit):
        f=repository.get_feature(c["candidate_id"])
        if not f: skipped.append(c["candidate_id"]); continue
        status,reasons,score=evaluate(c,f); s=repository.admit(c["candidate_id"],status,reasons,score); admitted.append(s)
    return {"admitted":len([x for x in admitted if x and x.get("active")]),"evaluated":len(admitted),"skipped_without_feature":skipped,"samples":admitted}
