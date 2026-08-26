from __future__ import annotations
import re
from content import generate as legacy_generate
from golden import repository
from compliance import policy as compliance_policy
from personal_ops import repository as personal_repository


def _grams(text: str,n: int=5) -> set[str]:
    s=re.sub(r"\s+","",text or "")
    s=re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]","",s).lower()
    return {s[i:i+n] for i in range(max(0,len(s)-n+1))}


def _copy_similarity(a: str,b: str) -> float:
    A=_grams(a); B=_grams(b)
    if not A or not B: return 0.0
    return len(A&B)/len(A)


def review(content: dict, topic: dict, insurance: dict, *, persona: dict | None=None,
           personal_materials: list[dict] | None=None) -> dict:
    issues=[]; warnings=[]
    title=(content.get("title") or "").strip(); body=(content.get("body") or "").strip(); cover=(content.get("cover_text") or "").strip()
    if len(title)<5: issues.append("标题过短或不完整")
    if len(title)>20: issues.append("标题超过20字")
    if len(body)<120: warnings.append("正文偏短，可能缺少可收藏的具体信息")
    knowledge_units=[row for row in (content.get("knowledge_units") or []) if isinstance(row,dict)]
    if len(knowledge_units)<18:
        warnings.append(f"可视化知识单元只有 {len(knowledge_units)} 个；建议补足原则、步骤、参数、对比和安全边界后再排版")
    repeated_units=len(knowledge_units)-len({(str(row.get("label") or ""),str(row.get("detail") or "")) for row in knowledge_units})
    if repeated_units:
        warnings.append(f"knowledge_units 中有 {repeated_units} 个重复信息单元")
    kw=(topic.get("heat_keyword") or topic.get("label") or "").replace("/","")
    if kw and not any(x in (title+body) for x in [kw,topic.get("label","")]): warnings.append("主题关键词在标题/正文中不明显")
    if cover and title and not (_grams(cover,2)&_grams(title,2)): warnings.append("封面文字与标题关联较弱")
    hits=legacy_generate.check_compliance(content,insurance)
    if hits: issues.append("合规红线: "+",".join(hits))
    policy=compliance_policy.evaluate(title,body,cover,
                                      aigc_label_required=bool((insurance or {}).get("aigc_label_required",False)))
    for item in policy.get("issues",[]):
        target=issues if item.get("severity")=="BLOCK" else warnings
        target.append(f"[{item.get('code')}] {item.get('message')}"+(f"：{item.get('evidence')}" if item.get("evidence") else ""))
    quality=compliance_policy.quality_assessment(title,body,cover)
    if quality.get("score",0)<.35: issues.append("内容质量分过低："+"；".join(quality.get("suggestions",[])))
    elif quality.get("score",0)<.55: warnings.append("内容质量仍可提升："+"；".join(quality.get("suggestions",[])))
    evidence=content.get("evidence") or {}; gids=evidence.get("golden_ids") or []
    sims=[]
    for gid in gids:
        s=repository.get_sample(gid)
        if not s: continue
        sim=_copy_similarity(title+"\n"+body,(s.get("title") or "")+"\n"+(s.get("body") or ""))
        sims.append({"golden_id":gid,"similarity":round(sim,4)})
    max_sim=max([x["similarity"] for x in sims] or [0])
    if max_sim>.28: issues.append(f"与某 Golden 原文 5-gram 重合度偏高({max_sim:.2f})，建议换表达/结构组合")
    elif max_sim>.18: warnings.append(f"与 Golden 原文存在一定文本重合({max_sim:.2f})")
    if not evidence.get("pattern_ids") and repository.list_patterns(): warnings.append("存在 Golden Pattern，但本稿未记录 Pattern evidence")
    voice_samples=personal_repository.writing_memory(personal_materials or [],limit=6,excerpt_chars=5000)
    voice_similarity=[]
    for sample in voice_samples:
        similarity=_copy_similarity(title+"\n"+body,sample.get("excerpt", ""))
        voice_similarity.append({"material_id":sample.get("material_id"),"similarity":round(similarity,4)})
    max_voice=max([item["similarity"] for item in voice_similarity] or [0])
    if max_voice>.22: issues.append(f"与个人语气样本出现过高逐字重合({max_voice:.2f})，应学习风格而不是复制原句")
    elif max_voice>.12: warnings.append(f"与个人语气样本存在一定逐字重合({max_voice:.2f})，建议进一步平台化改写")
    transition_count=sum(body.count(word) for word in ("首先","其次","再次","再者","综上所述"))
    sentences=[item.strip() for item in re.split(r"[。！？!?]",body) if item.strip()]
    average_sentence=round(sum(len(item) for item in sentences)/max(1,len(sentences)),1)
    if transition_count>=5: warnings.append("学术连接词使用偏多；应保留论证逻辑但改成更自然的小红书节奏")
    if average_sentence>42: warnings.append(f"平均句长约 {average_sentence} 字，手机端阅读可能偏密")
    status="REVISE" if issues else "PASS"
    return {"status":status,"issues":issues,"warnings":warnings,"policy":policy,"quality":quality,
            "copy_similarity":sims,"max_copy_similarity":round(max_sim,4),
            "voice_similarity":voice_similarity,"max_voice_similarity":round(max_voice,4),
            "style_checks":{"transition_count":transition_count,"average_sentence_chars":average_sentence,
                            "positioning":((persona or {}).get("account") or {}).get("positioning","")},
            "checks":{"title":bool(title),"body":bool(body),"cover":bool(cover),
                      "knowledge_units":len(knowledge_units),"evidence_golden":len(gids),
                      "evidence_patterns":len(evidence.get("pattern_ids") or [])}}
