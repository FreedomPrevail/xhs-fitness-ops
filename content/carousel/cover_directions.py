"""Generate three topic-specific, no-text cover directions."""
from __future__ import annotations

import hashlib
import json

from golden import repository, retriever
from golden.llm import call_json, is_configured, provider_name

ARCHETYPES = [
    ("editorial_still_life", "主题静物编辑", "premium editorial still-life", "warm cream, coral, lime", "single focal object, generous negative space"),
    ("toolkit_flatlay", "行动工具箱", "precise top-down fitness toolkit flat lay", "ivory, deep green, signal orange", "modular objects arranged as an actionable kit"),
    ("sunlit_space", "晨光训练空间", "sunlit home workout corner", "oat, terracotta, sage", "wide environmental scene with one clear focal zone"),
    ("paper_cut", "纸艺知识卡", "layered paper-cut editorial illustration", "cream, cobalt, coral", "bold geometric layers and a central symbol"),
    ("macro_equipment", "器械微距", "dramatic macro product photograph of training equipment", "charcoal, lime, warm white", "cropped diagonal object with strong texture"),
    ("motion_ribbon", "运动轨迹", "abstract motion ribbons inspired by exercise rhythm", "peach, burgundy, electric lime", "dynamic S-curve with calm text-safe area"),
    ("scrapbook", "训练手账", "refined scrapbook collage with blank cards and fitness objects", "recycled paper, coral, forest green", "asymmetric layered collage, no readable marks"),
    ("blueprint", "动作蓝图", "minimal instructional blueprint made of objects and abstract lines", "off-white, navy, aqua", "structured grid with one highlighted zone"),
    ("shadow_graphic", "光影几何", "architectural sunlight and equipment shadows", "sand, black, tomato red", "high-contrast shadow composition"),
    ("ingredient_board", "能量配方板", "clean ingredient-board composition using fitness and lifestyle objects", "butter yellow, green, coral", "balanced clusters around empty center"),
    ("checklist_scene", "完成清单", "three-dimensional checklist metaphor with blank cards and check tokens", "cream, mint, orange", "stepped composition expressing progress"),
    ("museum_plinth", "展台主视觉", "single fitness object on a sculptural museum plinth", "stone, plum, lime", "centered hero with premium studio lighting"),
]


def _common(title: str, tone: str) -> str:
    return (
        f"Theme: {title}. Tone: {tone}. Vertical 3:4 Xiaohongshu cover background. "
        "Reserve a large clean zone for Chinese typography added later. "
        "No text, letters, numbers, pseudo-writing, logos, watermark, or poster typography. "
        "A fully clothed non-identifiable adult fitness mascot is allowed when useful; no real-person likeness, "
        "minor, revealing body focus, medical claim, body measurement, or before-and-after comparison."
    )


def _fallback(title: str, tone: str, seed: str) -> list[dict]:
    digest=int(hashlib.sha256((title+"|"+seed).encode("utf-8")).hexdigest()[:12],16)
    start=digest % len(ARCHETYPES)
    step=5
    chosen=[ARCHETYPES[(start+i*step)%len(ARCHETYPES)] for i in range(3)]
    layouts=[("bottom_copy","upper two-thirds"),("top_copy","lower two-thirds"),("left_copy","right two-thirds")]
    common=_common(title,tone)
    out=[]
    for index,(item,layout) in enumerate(zip(chosen,layouts)):
        ident,name,mode,palette,composition=item
        text_zone,visual_zone=layout
        out.append({"id":ident,"name":name,"reason":f"围绕“{title[:18]}”使用{name}，与另外两案的媒介、构图和配色不同。",
                    "visual_mode":mode,"palette":palette,"composition":composition,
                    "text_zone":text_zone,"layout_variant":index,
                    "prompt":f"{common} {mode}; palette {palette}; {composition}; place the visual emphasis in the {visual_zone}."})
    return out


def build(outline: dict, visual_spec: dict, model_config: dict | None=None,
          novelty_seed: str="") -> list[dict]:
    """Return exactly three distinct cover directions; use LLM when configured."""
    title=str(outline.get("title") or outline.get("pages",[{}])[0].get("title","健身笔记"))[:48]
    tone=visual_spec.get("design_system",{}).get("tone","专业、温柔、可执行")
    positioning=str(visual_spec.get("account_positioning") or "")[:240]
    voice_profile=visual_spec.get("voice_profile") if isinstance(visual_spec.get("voice_profile"),dict) else {}
    fallback=_fallback(title,tone,novelty_seed)
    evidence=retriever.retrieve(title,k_samples=5,k_patterns=3,purpose="cover")
    current_patterns=list(evidence.get("patterns",[]))
    outline_pattern_ids=((outline.get("evidence") or {}).get("pattern_ids") or [])
    by_pattern={p.get("pattern_id"):p for p in repository.list_patterns() if p.get("pattern_id")}
    for pattern_id in outline_pattern_ids:
        pattern=by_pattern.get(pattern_id)
        if pattern and all(x.get("pattern_id")!=pattern_id for x in current_patterns):
            current_patterns.append(pattern)
        if len(current_patterns)>=3: break
    evidence["patterns"]=current_patterns[:3]
    pattern_ids=[p.get("pattern_id") for p in evidence.get("patterns",[]) if p.get("pattern_id")]
    golden_ids=[s.get("golden_id") for s in evidence.get("samples",[]) if s.get("golden_id")]
    pattern_pack=[{"pattern_id":p.get("pattern_id"),"segment":p.get("segment_key"),
                   "effective_weight":p.get("effective_weight"),
                   "cover_types":(p.get("pattern") or {}).get("cover_types",[]),
                   "cover_layouts":(p.get("pattern") or {}).get("cover_layouts",[]),
                   "cover_hooks":(p.get("pattern") or {}).get("cover_hooks",[]),
                   "title_patterns":(p.get("pattern") or {}).get("title_patterns",[]),
                   "hook_types":(p.get("pattern") or {}).get("hook_types",[]),
                   "content_structures":(p.get("pattern") or {}).get("content_structures",[])}
                  for p in evidence.get("patterns",[])]
    sample_pack=[]
    for sample in evidence.get("samples",[]):
        cover=((sample.get("features") or {}).get("cover") or {})
        sample_pack.append({"golden_id":sample.get("golden_id"),"cover_type":cover.get("cover_type"),
                            "layout":cover.get("layout"),"cover_hook":cover.get("cover_hook"),
                            "learnable_elements":cover.get("learnable_elements",[]),
                            "golden_score":sample.get("golden_score",0),
                            "transfer_score":sample.get("transfer_score",.5),
                            "revalidation_score":sample.get("revalidation_score",1.0),
                            "last_revalidated_at":sample.get("last_revalidated_at", ""),
                            "snapshot_count":len(repository.list_heat_snapshots(sample.get("golden_id", "")))})
    for row in fallback:
        row.update({"pattern_ids":pattern_ids,"golden_ids":golden_ids,"llm_provider":"fallback"})
    if not is_configured(model_config, role="cover"):
        return fallback
    pages=[{"title":p.get("title"),"type":p.get("type"),"key_takeaway":p.get("key_takeaway")}
           for p in outline.get("pages",[])[:7]]
    prompt=f'''你是小红书健身账号的 Cover Direction Agent。为同一个选题设计恰好3套高度差异化的无字封面主视觉。
三案必须在视觉媒介、主体隐喻、构图和主色上都明显不同，并与选题具体内容相关；不能只是换颜色。
图片模型只负责无文字背景，中文标题后续由模板渲染。需要人物时只使用非真人、全身着装完整的成年健身引导角色；禁止真人仿冒、未成年人、身体聚焦、Logo、水印、任何可读或伪造文字、医疗宣称和前后对比。
优先学习 effective_weight 高且 revalidation_score 仍有效的 Golden 封面结构；时效低或未复查样本只作弱参考。只学习结构与信息层级，不得复刻样本。
只返回JSON：{{"directions":[{{"id":"snake_case","name":"中文名","reason":"","visual_mode":"","palette":"","composition":"","text_zone":"bottom_copy|top_copy|left_copy","prompt":"完整英文图片提示词"}}]}}
选题：{title}
账号方向：{positioning}
语气：{tone}
稳定表达锚点：{json.dumps(voice_profile.get("stable_anchors",[]),ensure_ascii=False)}
页面信息：{json.dumps(pages,ensure_ascii=False)}
Golden Patterns：{json.dumps(pattern_pack,ensure_ascii=False)}
Golden Cover Samples：{json.dumps(sample_pack,ensure_ascii=False)}
随机差异种子：{novelty_seed}'''
    try:
        raw=call_json(prompt,model_config or {},schema="cover_directions.schema.json",role="cover")
    except Exception:
        return fallback
    rows=raw.get("directions") if isinstance(raw,dict) else None
    if not isinstance(rows,list):
        return fallback
    out=[]; seen=set()
    for index,row in enumerate(rows):
        if not isinstance(row,dict): continue
        ident=str(row.get("id") or f"direction_{index+1}")[:60]
        signature="|".join([str(row.get("visual_mode") or ""),str(row.get("composition") or ""),str(row.get("palette") or "")]).lower()
        if not signature.strip("|") or signature in seen: continue
        seen.add(signature)
        out.append({"id":ident,"name":str(row.get("name") or f"方案{index+1}")[:30],
                    "reason":str(row.get("reason") or "")[:240],"visual_mode":str(row.get("visual_mode") or "")[:160],
                    "palette":str(row.get("palette") or "")[:120],"composition":str(row.get("composition") or "")[:180],
                    "text_zone":row.get("text_zone") if row.get("text_zone") in ("bottom_copy","top_copy","left_copy") else ("bottom_copy","top_copy","left_copy")[min(index,2)],
                    "layout_variant":min(index,2),"pattern_ids":pattern_ids,"golden_ids":golden_ids,
                    "llm_provider":provider_name(model_config,"cover"),
                    "prompt":f"{_common(title,tone)} {str(row.get('prompt') or '')[:1600]}"})
        if len(out)==3: break
    for row in fallback:
        if len(out)>=3: break
        if row["id"] not in {x["id"] for x in out}: out.append(row)
    return out[:3]


def select(directions: list[dict], index: int | str | None=0) -> dict:
    try: picked=int(index or 0)
    except (TypeError,ValueError): picked=0
    return directions[max(0,min(picked,len(directions)-1))] if directions else {}
