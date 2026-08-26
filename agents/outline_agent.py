"""Turn an approved draft into a dense, modular 5–7 page infographic outline."""
from __future__ import annotations

import json
import re

from golden import repository, retriever
from golden.llm import call_json, is_configured

PAGE_TYPES = {"cover", "resonance", "step", "explain", "comparison", "warning", "checklist", "faq", "summary", "cta"}
MODULE_TYPES = {"step_flow", "comparison", "action_cards", "checklist", "fact_grid", "mistake_fix", "timeline", "formula", "faq", "summary"}


def _lines(text: str) -> list[str]:
    return [re.sub(r"^[#>*\-•▪➊➋➌\d️⃣\s]+", "", line).strip()
            for line in re.split(r"[\n。！？；!?;]+", text or "")
            if line.strip() and "AI辅助创作" not in line]


def _units(content: dict) -> list[dict]:
    rows=[]
    for index,item in enumerate(content.get("knowledge_units") or []):
        if not isinstance(item,dict):
            continue
        label=str(item.get("label") or "").strip()[:18]
        detail=str(item.get("detail") or "").strip()[:46]
        if label and detail:
            rows.append({**item,"unit_id":str(item.get("unit_id") or f"ku{index+1:02d}"),
                         "label":label,"detail":detail})
    if rows:
        return rows
    for index,value in enumerate(_lines(content.get("body", ""))[:24]):
        rows.append({"unit_id":f"ku{index+1:02d}","kind":"principle",
                     "label":value[:16],"detail":value[:46],"action":"","condition":"",
                     "safety_note":"","icon_hint":""})
    return rows


def _item(unit: dict) -> dict:
    value=str(unit.get("condition") or "").strip()[:24]
    cue=str(unit.get("action") or unit.get("safety_note") or "").strip()[:38]
    return {"label":str(unit.get("label") or "重点")[:18],"value":value,
            "detail":str(unit.get("detail") or "")[:46],"cue":cue,
            "tag":str(unit.get("kind") or "")[:12],"icon_hint":str(unit.get("icon_hint") or "")[:30],
            "source_unit_ids":[str(unit.get("unit_id") or "")] if unit.get("unit_id") else []}


def _module(module_id: str, kind: str, title: str, rows: list[dict], *, columns: list[dict] | None=None) -> dict:
    return {"module_id":module_id,"type":kind,"title":title[:18],"note":"",
            "items":[_item(row) for row in rows[:6]],"columns":columns or []}


def _take(rows: list[dict], start: int, count: int) -> list[dict]:
    picked=rows[start:start+count]
    if picked:
        return picked
    return rows[:count] or [{"unit_id":"fallback","label":"从可执行的一步开始","detail":"根据自己的基础逐步调整，不追求一次做到满分"}]


def _fallback(topic: dict, content: dict) -> dict:
    rows=_units(content)
    title=(content.get("title") or topic.get("label") or "健身入门").strip()[:32]
    count=max(1,len(rows)); groups=[]
    for index in range(5):
        start=round(index*count/5); end=round((index+1)*count/5)
        groups.append(rows[start:end])
    first,second,third,fourth,fifth=[group or _take(rows,0,1) for group in groups]
    comparison_source=third[:4]; midpoint=max(1,(len(comparison_source)+1)//2)
    comparison_columns=[
      {"title":"先判断","items":[str(x.get("label") or "")[:34] for x in comparison_source[:midpoint]]},
      {"title":"再行动","items":[str(x.get("detail") or "")[:34] for x in comparison_source[midpoint:] or comparison_source[:2]]},
    ]
    p2_modules=[_module("p2_flow","step_flow","执行顺序",first[:4])]
    if len(first)>4: p2_modules.append(_module("p2_grid","fact_grid","顺序之外",first[4:]))
    p3_modules=[_module("p3_actions","action_cards","具体怎么做",second[:3])]
    if len(second)>3: p3_modules.append(_module("p3_checks","checklist","执行要点",second[3:]))
    p5_modules=[_module("p5_grid","fact_grid","关键细节",fourth[:4])]
    if len(fourth)>4: p5_modules.append(_module("p5_safe","checklist","安全边界",fourth[4:]))
    p4_modules=[_module("p4_compare","comparison","判断与行动",[],columns=comparison_columns)]
    if len(third)>4: p4_modules.append(_module("p4_extra","fact_grid","补充判断",third[4:]))
    p6_modules=[_module("p6_check","checklist","行动清单",fifth[:4])]
    if len(fifth)>4: p6_modules.append(_module("p6_summary","summary","最后确认",fifth[4:]))
    pages=[
      {"page_no":1,"type":"cover","purpose":"吸引点击并说清收益","title":title,
       "subtitle":content.get("cover_text", "保存下来照着做")[:50],"badge":"新手指南","bullets":[],
       "density":"light","info_unit_count":0,"modules":[],"key_takeaway":"一张图说清主题",
       "scene":"统一账号角色与训练道具，给中文标题留出干净区域","needs_visual":True,"visual_role":"hero"},
      {"page_no":2,"type":"step","purpose":"先建立完整行动路线","title":"先看完整路线",
       "subtitle":"顺序比堆强度更重要","badge":"路线图","bullets":[x["label"] for x in first],
       "density":"high","modules":p2_modules,
       "key_takeaway":"先知道每一步的作用","scene":"四个无字训练或生活方式小场景横向排列","needs_visual":True,"visual_role":"diagram"},
      {"page_no":3,"type":"explain","purpose":"给出可直接执行的动作卡","title":"照着这三步做",
       "subtitle":"每次只抓一个重点","badge":"动作卡","bullets":[x["detail"] for x in second],
       "density":"high","modules":p3_modules,
       "key_takeaway":"把抽象建议变成动作","scene":"三个一致画风的无字动作小插画，横向分开","needs_visual":True,"visual_role":"supporting"},
      {"page_no":4,"type":"comparison","purpose":"帮助读者按条件选择","title":"两种情况怎么选",
       "subtitle":"先看条件，再看动作","badge":"对照表","bullets":[],"density":"high",
       "modules":p4_modules,
       "key_takeaway":"不同基础不套同一答案","scene":"两个对照的无字生活方式小场景","needs_visual":True,"visual_role":"comparison"},
      {"page_no":5,"type":"warning","purpose":"补足细节和安全边界","title":"这些细节别忽略",
       "subtitle":"做对比做多更重要","badge":"避坑","bullets":[x["detail"] for x in fourth],
       "density":"high","modules":p5_modules,
       "key_takeaway":"给方法加上适用条件","scene":"四个无字安全提示图标或器材细节","needs_visual":True,"visual_role":"icon_only"},
      {"page_no":6,"type":"summary","purpose":"形成可收藏行动清单","title":"最后保存这张清单",
       "subtitle":"下次行动前打开看","badge":"收藏页","bullets":[x["label"] for x in fifth],
       "density":"high","modules":p6_modules,
       "key_takeaway":"今天选择一项开始","scene":"无字清单、勾选标记和运动小物件","needs_visual":True,"visual_role":"icon_only"},
    ]
    for page in pages:
        page["info_unit_count"]=_page_info_units(page)
    return {"topic_id":topic.get("id","manual"),"title":title,
            "content_goal":"把完整干货拆成可读、可保存、图文并茂的信息图",
            "target_audience":"健身新手","page_count":len(pages),"pages":pages,
            "evidence":{"pattern_ids":content.get("evidence",{}).get("pattern_ids",[]),
                        "golden_ids":content.get("evidence",{}).get("golden_ids",[]),
                        "account_signals":[],"transfer_dimensions":{}}}


def _page_info_units(page: dict) -> int:
    total=0
    for module in page.get("modules") or []:
        total+=len(module.get("items") or [])
        total+=sum(len(column.get("items") or []) for column in (module.get("columns") or []))
    return total


def _normalize_module(module: dict, page_no: int, index: int) -> dict | None:
    if not isinstance(module,dict):
        return None
    kind=str(module.get("type") or "fact_grid")
    if kind not in MODULE_TYPES:
        kind="fact_grid"
    items=[]
    for item in (module.get("items") or [])[:6]:
        if not isinstance(item,dict) or not str(item.get("label") or "").strip():
            continue
        items.append({"label":str(item.get("label"))[:18],"value":str(item.get("value") or "")[:24],
                      "detail":str(item.get("detail") or "")[:46],"cue":str(item.get("cue") or "")[:38],
                      "tag":str(item.get("tag") or "")[:12],"icon_hint":str(item.get("icon_hint") or "")[:30],
                      "source_unit_ids":[str(x)[:16] for x in (item.get("source_unit_ids") or [])[:4]]})
    columns=[]
    for column in (module.get("columns") or [])[:2]:
        if isinstance(column,dict):
            columns.append({"title":str(column.get("title") or "")[:16],
                            "items":[str(x)[:34] for x in (column.get("items") or [])[:5]]})
    if not items and not any(column.get("items") for column in columns):
        return None
    return {"module_id":str(module.get("module_id") or f"p{page_no}m{index+1}")[:20],
            "type":kind,"title":str(module.get("title") or "")[:18],
            "note":str(module.get("note") or "")[:48],"items":items,"columns":columns}


def build(topic: dict, content: dict, model_config: dict | None = None,
          persona: dict | None = None, personal_materials: list[dict] | None = None) -> dict:
    """Use the routed Outline provider; always retain a dense deterministic fallback."""
    fallback=_fallback(topic,content)
    if not is_configured(model_config,role="outline"):
        return fallback
    query=" ".join([str(topic.get("label") or ""),str(content.get("title") or "")]).strip() or "健身"
    retrieved=retriever.retrieve(query,5,3,purpose="content")
    pattern_pack=[{"pattern_id":p.get("pattern_id"),"segment":p.get("segment_key"),
                   "effective_weight":p.get("effective_weight"),
                   "content_structures":(p.get("pattern") or {}).get("content_structures",[]),
                   "writing_styles":(p.get("pattern") or {}).get("writing_styles",[])}
                  for p in retrieved.get("patterns",[])]
    sample_pack=[{"golden_id":s.get("golden_id"),"title":s.get("title"),
                  "golden_score":s.get("golden_score",0),"transfer_score":s.get("transfer_score",.5),
                  "revalidation_score":s.get("revalidation_score",1.0),
                  "snapshot_count":len(repository.list_heat_snapshots(s.get("golden_id",""))),
                  "content_structure":((s.get("features") or {}).get("content") or {}).get("content_structure",[])}
                 for s in retrieved.get("samples",[])]
    account_voice=((persona or {}).get("account") or {}).get("voice_profile",{})
    memory_titles=[str(item.get("title") or "") for item in (personal_materials or [])
                   if item.get("kind") in ("writing_sample","voice_sample")
                   and item.get("rights_status") in ("owned","authorized")][:6]
    prompt=f'''你是小红书信息图 Outline Agent。把正文和 knowledge_units 规划成5–7页高信息密度图文，只返回JSON。
第一页是封面且 modules=[]。其余每页使用1–3个 modules，每页承载5–10个不重复的原子信息；标题≤16汉字，单条≤46字。不要为了密度重复或发明事实。
module.type只能是 step_flow/comparison/action_cards/checklist/fact_grid/mistake_fix/timeline/formula/faq/summary。流程、双栏对比、三动作卡、误区纠正、参数网格和清单要根据内容真实轮换，不能每页同版。
每个 item 必须保留 source_unit_ids，指向输入 knowledge_units。数字、效果与安全边界不得脱离原文；信息不足时宁可减少模块，不得编造。
页面总文字适合1080×1440手机图：每页约70–150汉字，至少25%留白。图片模型只画无字插画，本地模板负责所有中文。
根据 Golden Pattern 的有效权重和样本 golden/transfer/revalidation 信号优化节奏；低时效样本只作弱证据，不得复刻原文或声称实时热点。
保留账号“先判断—给证据—给行动—讲限制”的节奏，但不要复刻个人文章句子。
Topic:{topic.get("label","")}
Title:{content.get("title","")}
Body:{content.get("body","")}
Knowledge Units:{json.dumps(_units(content),ensure_ascii=False)}
Account Voice:{json.dumps(account_voice,ensure_ascii=False)}
Voice-only Sample Titles:{json.dumps(memory_titles,ensure_ascii=False)}
Golden Patterns:{json.dumps(pattern_pack,ensure_ascii=False)}
Golden Samples:{json.dumps(sample_pack,ensure_ascii=False)}
返回字段严格为 topic_id,title,content_goal,target_audience,page_count,pages,evidence。page包含 page_no,type,purpose,title,subtitle,badge,bullets,density,info_unit_count,modules,key_takeaway,scene,needs_visual,visual_role。module包含 module_id,type,title,note,items,columns。'''
    try:
        out=call_json(prompt,model_config or {},schema="outline_agent_output.schema.json",role="outline")
    except Exception:
        return fallback
    if not isinstance(out,dict) or not 5<=len(out.get("pages",[]))<=7:
        return fallback
    clean_pages=[]
    for page_index,page in enumerate(out["pages"],1):
        if not isinstance(page,dict):
            return fallback
        kind=str(page.get("type") or "explain")
        if kind not in PAGE_TYPES:
            kind="explain"
        modules=[]
        for module_index,module in enumerate((page.get("modules") or [])[:4]):
            normalized=_normalize_module(module,page_index,module_index)
            if normalized:
                modules.append(normalized)
        if page_index==1:
            kind="cover"; modules=[]
        elif not modules:
            fallback_page=fallback["pages"][min(page_index-1,len(fallback["pages"])-1)]
            modules=fallback_page.get("modules",[])
        clean={"page_no":page_index,"type":kind,"purpose":str(page.get("purpose") or "传达可执行信息")[:80],
               "title":str(page.get("title") or f"第{page_index}页重点")[:32],
               "subtitle":str(page.get("subtitle") or "")[:50],"badge":str(page.get("badge") or "")[:12],
               "bullets":[str(x)[:36] for x in (page.get("bullets") or [])[:6]],
               "density":"light" if page_index==1 else (page.get("density") if page.get("density") in ("medium","high") else "high"),
               "modules":modules,"key_takeaway":str(page.get("key_takeaway") or "")[:60],
               "scene":str(page.get("scene") or "无字信息图插画")[:220],
               "needs_visual":bool(page.get("needs_visual",True)),
               "visual_role":page.get("visual_role") if page.get("visual_role") in ("hero","supporting","diagram","comparison","icon_only","background","none") else "supporting"}
        clean["info_unit_count"]=_page_info_units(clean)
        clean_pages.append(clean)
    if clean_pages[-1]["type"] not in ("checklist","summary","cta"):
        clean_pages[-1]["type"]="summary"
    evidence=out.get("evidence") if isinstance(out.get("evidence"),dict) else {}
    allowed_patterns=[p.get("pattern_id") for p in retrieved.get("patterns",[]) if p.get("pattern_id")]
    allowed_golden=[s.get("golden_id") for s in retrieved.get("samples",[]) if s.get("golden_id")]
    evidence["pattern_ids"]=[x for x in evidence.get("pattern_ids",[]) if x in allowed_patterns] or allowed_patterns
    evidence["golden_ids"]=[x for x in evidence.get("golden_ids",[]) if x in allowed_golden] or allowed_golden
    evidence.setdefault("account_signals",[]); evidence.setdefault("transfer_dimensions",{})
    return {"topic_id":topic.get("id",out.get("topic_id","manual")),
            "title":str(out.get("title") or content.get("title") or topic.get("label") or "健身指南")[:40],
            "content_goal":str(out.get("content_goal") or "高密度信息图")[:160],
            "target_audience":str(out.get("target_audience") or "健身新手")[:80],
            "page_count":len(clean_pages),"pages":clean_pages,"evidence":evidence}
