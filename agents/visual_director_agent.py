"""Map modular content to deterministic infographic templates and image roles."""
from __future__ import annotations

from typing import Any

TEMPLATE_BY_MODULE={
  "step_flow":"infographic_flow","comparison":"infographic_compare",
  "action_cards":"infographic_actions","checklist":"infographic_checklist",
  "fact_grid":"infographic_grid","mistake_fix":"infographic_mistakes",
  "timeline":"infographic_timeline","formula":"infographic_formula",
  "faq":"infographic_faq","summary":"infographic_summary",
}
LAYOUT_BY_TEMPLATE={
  "infographic_flow":"stacked_modules","infographic_compare":"split_compare",
  "infographic_actions":"three_card_grid","infographic_grid":"two_by_two_grid",
  "infographic_checklist":"centered_checklist","infographic_mistakes":"split_compare",
  "infographic_timeline":"vertical_timeline","infographic_formula":"stacked_modules",
  "infographic_faq":"stacked_modules","infographic_summary":"centered_checklist",
}


def _primary_module(page: dict) -> str:
    types=[str(module.get("type") or "") for module in (page.get("modules") or [])]
    for preferred in ("action_cards","comparison","mistake_fix","step_flow","timeline","formula","faq","fact_grid","checklist","summary"):
        if preferred in types:
            return preferred
    if types:
        return types[0]
    return {"comparison":"comparison","warning":"mistake_fix","checklist":"checklist",
            "faq":"faq","summary":"summary","cta":"checklist","step":"step_flow"}.get(
                str(page.get("type") or ""),"fact_grid")


def build(outline: dict[str, Any], persona: dict | None = None) -> dict[str, Any]:
    account=(persona or {}).get("account",{})
    pages=[]
    for item in outline["pages"]:
        is_cover=item["type"]=="cover"
        primary=_primary_module(item)
        module_types=[str(module.get("type") or "") for module in (item.get("modules") or [])] or [primary]
        template="cover_scene" if is_cover else TEMPLATE_BY_MODULE.get(primary,"infographic_grid")
        layout="top_title_bottom_visual" if is_cover else LAYOUT_BY_TEMPLATE.get(template,"stacked_modules")
        needs_asset=bool(item.get("needs_visual")) and item.get("visual_role")!="none"
        labels=[str(entry.get("label") or "") for module in (item.get("modules") or []) for entry in (module.get("items") or [])][:6]
        scene="；".join([str(item.get("scene") or ""),"模块:"+",".join(module_types),"信息:"+",".join(labels)])[:390]
        slots=[]
        if needs_asset:
            slots=[{"slot_id":"main","role":"hero" if is_cover else "illustration_strip","required":True,
                    "placement":"upper_visual" if is_cover else "module_illustration_band",
                    "safe_text_area":"lower third" if is_cover else "no text; isolated vignette cells"}]
        pages.append({"page_no":item["page_no"],"page_type":item["type"],
                      "template_type":template,"layout":layout,"module_types":module_types,
                      "text_emphasis":[item["title"][:18]],
                      "text_density":"light" if is_cover else ("dense" if item.get("info_unit_count",0)>=6 else "medium"),
                      "asset_slots":slots,"scene_prompt":scene,
                      "render_notes":["Generated asset must not contain text",
                                      "Chinese text is rendered by local components",
                                      "Keep at least 25 percent visual breathing room"]})
    return {"design_system":{
              "style_name":"pastel_editorial_infographic_v2",
              "tone":"专业但不端着，温柔、清晰、信息丰富且可执行",
              "palette":{"background":"#FFF9EE","primary":"#7A4A2E","accent":"#F2B46D","text":"#4D321F"},
              "typography_density":"dense",
              "consistency_rules":["奶油纸张底与暖棕描边贯穿内容页","所有中文只由本地模板渲染",
                                   "每页2至4个视觉层级而不是一张大图","插画与文字共同解释信息"]},
            "pages":pages,"account_positioning":account.get("positioning",""),
            "voice_profile":account.get("voice_profile",{})}
