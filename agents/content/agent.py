from __future__ import annotations
import json, re, uuid
from content import generate as legacy_generate
from golden import retriever, repository
from golden.llm import call_json, provider_name
from personal_ops import repository as personal_repository


def _validated_evidence_ids(reported, allowed: list[str]) -> list[str]:
    """Keep only IDs Retriever supplied and recover omissions/empty arrays."""
    allowed=[str(x) for x in allowed if x]
    allowed_set=set(allowed)
    reported=reported if isinstance(reported,list) else []
    valid=[]
    for item in reported:
        item=str(item)
        if item in allowed_set and item not in valid:
            valid.append(item)
    return valid or allowed


def _sample_evidence(s: dict) -> dict:
    f=s.get("features",{})
    return {
      "golden_id":s["golden_id"],"title":s.get("title", ""),
      "content_type":s.get("content_type","unknown"),"analysis_scope":s.get("analysis_scope","metadata_only"),
      "body_excerpt":(s.get("body") or "")[:900],
      "why_selected":f.get("analysis_summary",{}).get("why_selected", ""),
      "title_pattern":f.get("title_hook",{}).get("title_pattern", ""),
      "content_structure":f.get("content",{}).get("content_structure",[]),
      "audience_intent":f.get("audience_intent",{}),
      "content_features":f.get("content",{}),"copy_features":f.get("title_hook",{}),
      "cover_features":f.get("cover",{}),
      "platform_performance":{"likes":s.get("likes",0),"collects":s.get("collects",0),"comments":s.get("comments",0)},
      "transfer_dimensions":s.get("transfer_dimensions",{}),
      "golden_score":s.get("golden_score",0),
      "transfer_score":s.get("transfer_score",.5),
      "revalidation_score":s.get("revalidation_score",1.0),
      "last_revalidated_at":s.get("last_revalidated_at", ""),
      "revalidation_snapshot_count":len(repository.list_heat_snapshots(s["golden_id"])),
    }


def _merge_selected_golden(evidence: dict, refs: list[dict], limit: int=5) -> dict:
    """把用户在“选题文案搜索”中勾选的Golden样本放进证据包。

    Retriever仍负责自动召回；人工勾选只改变证据优先级，不会绕开
    Golden Pool，也不会把平台实时文案伪装成Golden Sample。
    """
    selected=[]
    for ref in refs:
        golden_id=str(ref.get("golden_id") or "").strip()
        if not golden_id:
            continue
        sample=repository.get_sample(golden_id)
        if sample and retriever.content_sample_eligible(sample) and all(x.get("golden_id") != golden_id for x in selected):
            selected.append(sample)
    existing=[s for s in evidence.get("samples",[]) if all(
        s.get("golden_id") != x.get("golden_id") for x in selected)]
    return {**evidence,"samples":[*selected,*existing][:max(limit,len(selected))]}


def _topic_brief(topic: dict) -> dict:
    """只把Topic Agent的新协议传给模型，隔离旧title_patterns等模板字段。"""
    source=topic.get("topic_brief") if isinstance(topic.get("topic_brief"),dict) else {}
    keyword=topic.get("heat_keyword") or topic.get("label") or "健身"
    return {
      "topic":str(source.get("topic") or topic.get("label") or ""),
      "angle":str(source.get("angle") or ""),
      "audience":source.get("audience") if isinstance(source.get("audience"),dict) else {},
      "intent":str(source.get("intent") or "mixed"),
      "timing_hypothesis":source.get("timing_hypothesis") if isinstance(source.get("timing_hypothesis"),dict) else {},
      "keywords":[str(x) for x in (source.get("keywords") or [keyword]) if str(x).strip()],
      "pattern_ids":[str(x) for x in (source.get("pattern_ids") or []) if str(x).strip()],
      "score":source.get("score"),
      "reason":str(source.get("reason") or ""),
    }


def _normalize_knowledge_units(rows, body: str) -> list[dict]:
    """Keep compact atomic facts/actions so Outline does not have to mine prose."""
    allowed={"principle","step","parameter","comparison","mistake","safety","check","example","summary"}
    clean=[]
    for index,item in enumerate(rows if isinstance(rows,list) else []):
        if not isinstance(item,dict):
            continue
        label=str(item.get("label") or "").strip()[:18]
        detail=str(item.get("detail") or "").strip()[:52]
        if not label or not detail:
            continue
        kind=str(item.get("kind") or "principle")
        clean.append({
          "unit_id":str(item.get("unit_id") or f"ku{index+1:02d}")[:16],
          "kind":kind if kind in allowed else "principle",
          "label":label,"detail":detail,
          "condition":str(item.get("condition") or "").strip()[:42],
          "action":str(item.get("action") or "").strip()[:42],
          "safety_note":str(item.get("safety_note") or "").strip()[:52],
          "icon_hint":str(item.get("icon_hint") or "").strip()[:30],
          "basis":item.get("basis") if item.get("basis") in ("general_knowledge","provided_source","operator_confirmed","unknown") else "unknown",
        })
        if len(clean)>=36:
            break
    if clean:
        return clean
    fragments=[]
    for raw in re.split(r"[\n。！？；!?;]+",body or ""):
        value=re.sub(r"^[#>*\-•▪➊➋➌\d️⃣\s]+","",raw).strip()
        if 8<=len(value)<=80 and "AI辅助创作" not in value:
            fragments.append(value)
    for index,value in enumerate(fragments[:24]):
        clean.append({"unit_id":f"ku{index+1:02d}","kind":"principle",
                      "label":value[:14],"detail":value[:52],"condition":"","action":"",
                      "safety_note":"","icon_hint":"","basis":"unknown"})
    return clean


def build_prompt(topic: dict, persona: dict, insurance: dict, refs: list[dict], evidence: dict,
                 personal_materials: list[dict] | None=None,
                 selected_material_ids: list[str] | None=None) -> str:
    topic_brief=_topic_brief(topic)
    sample_pack=[_sample_evidence(s) for s in evidence["samples"]]
    pattern_pack=[{"pattern_id":p["pattern_id"],"segment":p["segment_key"],
                   "effective_weight":p.get("effective_weight"),"updated_at":p.get("updated_at"),
                   "pattern":p.get("pattern",{})} for p in evidence["patterns"]]
    manual_refs=[]
    for r in refs[:5]:
        if not r.get("title"):
            continue
        manual_refs.append({
          "source":r.get("source") or "platform_live",
          "content_type":r.get("content_type") or "unknown",
          "title":r.get("title", ""),
          "body_excerpt":(r.get("body_excerpt") or "")[:600]
            if r.get("source") != "golden_sample" else
              ("已进入Golden Samples图文证据包" if r.get("content_type")!="video" else "视频只作为选题/Hook弱证据，不进入图文正文证据包"),
        })
    is_insurance=topic["category"] in ("health_risk","insurance_soft")
    compliance=insurance["compliance_redlines"] if is_insurance else {}
    compliance={**compliance,"evidence_output_requirement":
      "在evidence.used_features中分别返回topic_features/audience_features/content_features/copy_features；每项含真实golden_id与采用的feature路径。不要虚构未提供的ID。"}
    voice_memory=personal_repository.writing_memory(
        personal_materials or [], selected_ids=selected_material_ids, limit=6, excerpt_chars=1800)
    return f'''你是 Content Agent。根据 Topic Brief + Golden Pattern + Golden Sample Evidence 写一篇原创小红书健身/健康笔记。只输出合法 JSON，不要 markdown。\n\n核心规则：\n1) Golden 原文是证据层：可学习选题角度、信息密度、论证方式、段落节奏、可收藏的具体信息，但不得逐句改写、不得围绕单一原文做近似复刻。\n2) 优先服从 Golden Pattern 的高权重结构；多个 sample 交叉学习，避免单样本模仿。\n3) 输出必须面向 Topic Brief 的 audience + intent；搜索型内容自然覆盖关键词，推荐流内容强化 hook。\n4) 正文必须有具体、可执行健身信息，不要只写泛泛励志。正文之外再给18–36个 knowledge_units，把原则、步骤、参数、对比、误区和安全边界拆成原子信息；每个 unit 只表达一个事实或动作，不能换词凑数。\n5) 数字、次数、时长和效果必须有合理依据及适用条件；不确定就不写精确数字。Golden 只证明结构和表达有效，不能充当健身事实来源。\n6) 人工勾选的搜索证据只用于理解当下痛点、信息点和热门角度，不得逐句改写或模仿标题句式。\n7) 不使用旧版固定title_patterns模板；public文案里不要出现golden_id/pattern_id。\n8) transfer_dimensions 是该 Golden 样本在我们账号已发笔记回流后的分维验证表现(topic/audience/content/copy/cover)；表现好的维度优先学习其结构、信息点与论证方式，不要照搬低分维度的角度。\n9) revalidation_score 是人工热度快照计算的独立时效信号。优先学习仍持续增长且快照充分的样本；低分或久未复查的样本只能作为弱证据。不得把缓存证据称为实时热点。\n10) 个人写作样本只用于学习“先判断、再用数字/对比/细节解释、最后说明限制”的语气与论证节奏。不得复制句子，不得把样本中的社会议题、旧数据或人物当作健身事实，也不得据此虚构本人健身经历。长篇学术表达必须改写成手机端短段落和可执行建议。\n\nTopic Brief：{json.dumps(topic_brief,ensure_ascii=False)}\nPersona：{json.dumps(persona.get("account",{}),ensure_ascii=False)}\n个人写作样本（voice-only）：{json.dumps(voice_memory,ensure_ascii=False)}\nGolden Patterns：{json.dumps(pattern_pack,ensure_ascii=False)}\nGolden Samples：{json.dumps(sample_pack,ensure_ascii=False)}\n人工勾选的选题搜索证据：{json.dumps(manual_refs,ensure_ascii=False)}\n合规：{json.dumps(compliance,ensure_ascii=False)}\n\n严格返回：\n{{"title":"≤20字完整标题","body":"完整正文","topics":["话题1","话题2","话题3"],"cover_text":"封面主文案","knowledge_units":[{{"unit_id":"ku01","kind":"principle|step|parameter|comparison|mistake|safety|check|example|summary","label":"≤18字","detail":"≤52字","condition":"适用条件","action":"下一步动作","safety_note":"安全边界","icon_hint":"无字图标提示","basis":"general_knowledge|provided_source|operator_confirmed|unknown"}}],"evidence":{{"pattern_ids":{json.dumps([p["pattern_id"] for p in evidence["patterns"]],ensure_ascii=False)},"golden_ids":{json.dumps([s["golden_id"] for s in evidence["samples"]],ensure_ascii=False)},"used_moves":["实际采用的结构/写法1","实际采用的写法2"]}}}}'''


def generate(topic: dict, refs: list[dict], model_config: dict, persona: dict, insurance: dict,
             personal_materials: list[dict] | None=None,
             selected_material_ids: list[str] | None=None) -> dict:
    brief=_topic_brief(topic)
    query=" ".join([brief.get("topic", ""),*brief.get("keywords",[]) ]).strip() or "健身"
    ev=retriever.retrieve(query,5,3,purpose="content")
    ev=_merge_selected_golden(ev,refs,5)
    prompt=build_prompt(topic,persona,insurance,refs,ev,personal_materials,selected_material_ids)
    out=call_json(prompt,model_config,schema="content_agent_output.schema.json",role="content")
    for k in ("title","body","topics","cover_text"): out.setdefault(k,[] if k=="topics" else "")
    out["knowledge_units"]=_normalize_knowledge_units(out.get("knowledge_units"),out.get("body", ""))
    model_evidence=out.get("evidence") if isinstance(out.get("evidence"),dict) else {}
    retrieved_pattern_ids=[p["pattern_id"] for p in ev["patterns"]]
    retrieved_golden_ids=[s["golden_id"] for s in ev["samples"]]
    used_moves=model_evidence.get("used_moves") if isinstance(model_evidence.get("used_moves"),list) else []
    raw_used=model_evidence.get("used_features") if isinstance(model_evidence.get("used_features"),dict) else {}
    used_features={}; allowed_gids=set(retrieved_golden_ids)
    for group in ("topic_features","audience_features","content_features","copy_features"):
        clean=[]
        for item in raw_used.get(group,[]) if isinstance(raw_used.get(group),list) else []:
            if not isinstance(item,dict) or str(item.get("golden_id") or "") not in allowed_gids: continue
            feature=str(item.get("feature") or "").strip()
            if not feature: continue
            clean.append({"golden_id":str(item.get("golden_id")),"feature":feature[:120]})
        if not clean:
            clean=[{"golden_id":gid,"feature":"retrieval_fallback"} for gid in retrieved_golden_ids]
        used_features[group]=clean[:10]
    out["evidence"]={
      "pattern_ids":_validated_evidence_ids(model_evidence.get("pattern_ids"),retrieved_pattern_ids),
      "golden_ids":_validated_evidence_ids(model_evidence.get("golden_ids"),retrieved_golden_ids),
      "used_moves":[str(x).strip() for x in used_moves if str(x).strip()],
      "used_features":used_features,
    }
    content=legacy_generate.postprocess(out,topic,persona,insurance)
    content["evidence"]=out["evidence"]
    generation_id="gen_"+uuid.uuid4().hex[:12]
    usage_id=repository.record_usage(generation_id,topic.get("id",""),content["evidence"].get("golden_ids",[]),content["evidence"].get("pattern_ids",[]),{"used_moves":content["evidence"].get("used_moves",[]),"title":content.get("title",""),**used_features})
    content["generation_id"]=generation_id; content["golden_usage_id"]=usage_id
    content["llm_provider"]=provider_name(model_config,"content")
    content["personal_memory"]={
      "mode":"voice_and_argumentation_style_only",
      "material_ids":[row.get("material_id") for row in personal_repository.writing_memory(
          personal_materials or [],selected_ids=selected_material_ids,limit=6,excerpt_chars=200)],
    }
    return content
