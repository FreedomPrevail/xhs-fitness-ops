"""Provider-neutral, no-text visual task planner for modular infographics."""
from __future__ import annotations

PAGE_VISUAL_LANGUAGE={
  "step_flow":"four equal, tightly framed vignette panels in a clean 2-by-2 grid",
  "comparison":"two balanced illustrated vignette groups with visibly different objects; balanced split composition",
  "action_cards":"four equal, tightly framed vignette panels in a clean 2-by-2 grid; unused cells may stay blank",
  "checklist":"four equal tactile checklist vignettes in a clean 2-by-2 grid",
  "fact_grid":"four coordinated icon-like editorial vignettes arranged as a two-by-two grid",
  "mistake_fix":"four equal paired mistake-and-correction vignette panels in a clean 2-by-2 grid without body transformation",
  "timeline":"four small chronological vignettes connected by a gentle visual path",
  "formula":"three ingredient or training-component clusters combining into one practical result",
  "faq":"three paired question-and-answer object vignettes using blank cards and clear visual contrast",
  "summary":"one compact toolkit scene containing four recognizable takeaway objects",
}


def build(post_id: str, visual_spec: dict, cover_directions: list[dict] | None=None,
          selected_index: int=0) -> dict:
    style=visual_spec["design_system"]
    tasks=[]
    directions=list(cover_directions or [])[:3]
    for index,direction in enumerate(directions):
        asset_id=f"{post_id}_cover_{index+1:02d}"
        tasks.append({"asset_id":asset_id,"page_no":1,"slot_id":f"cover_{index+1:02d}",
                      "type":"cover_variant","variant_index":index,
                      "cover_direction_id":direction.get("id"),"prompt":direction.get("prompt",""),
                      "negative_prompt":"Chinese characters, English words, numbers, pseudo-writing, logo, watermark, identifiable real person, minor, body transformation, before-and-after collage",
                      "composition":direction.get("composition",""),"aspect_ratio":"3:4","status":"planned",
                      "output_path":f"assets/{asset_id}.png","generation_metadata":{}})
    for page in visual_spec["pages"]:
        if page["page_no"]==1:
            continue
        module_types=list(page.get("module_types") or [])
        primary=module_types[0] if module_types else "fact_grid"
        language=PAGE_VISUAL_LANGUAGE.get(primary,PAGE_VISUAL_LANGUAGE["fact_grid"])
        for slot in page.get("asset_slots",[]):
            asset_id=f"{post_id}_p{page['page_no']:02d}_{slot['slot_id']}"
            prompt=(f"Vertical 3:4 no-text visual asset for a Chinese fitness infographic. "
                    f"Style: warm pastel editorial gouache, cream paper texture, soft peach/mint/sky-blue fills, "
                    f"warm brown outlines, polished sticker-like edges, consistent adult fitness-guide mascot when a person is useful. "
                    f"Create {language}. Scene brief: {page['scene_prompt']}. "
                    "Keep every vignette visually separated with wide plain gutters so the local renderer can crop it into information modules. "
                    "Each person or main object must fill 65 to 80 percent of its own panel height; use a close full-body or medium-full framing, never tiny distant figures. "
                    "Do not place a second miniature montage inside any panel. Keep the panel grid geometrically regular. "
                    "Use fully clothed non-identifiable adults only; keep anatomy natural and instructions visually safe. "
                    "No text, letters, numbers, pseudo-writing, logo or watermark.")
            tasks.append({"asset_id":asset_id,"page_no":page["page_no"],"slot_id":slot["slot_id"],
                          "type":"illustration_strip","module_types":module_types,"prompt":prompt,
                          "negative_prompt":"Chinese characters, English words, numbers, pseudo-writing, logo, watermark, identifiable real person, celebrity, minor, revealing clothing, body measurement, medical claim, body transformation, before-and-after collage",
                          "composition":slot["placement"],"aspect_ratio":"3:4","status":"planned",
                          "output_path":f"assets/{asset_id}.png","generation_metadata":{}})
    selected=max(0,min(int(selected_index or 0),max(0,len(directions)-1))) if directions else 0
    selected_asset=f"{post_id}_cover_{selected+1:02d}" if directions else ""
    return {"post_id":post_id,"style_lock":{
              "visual_mode":"pastel_editorial_infographic",
              "character_consistency":"内容页保持同一位非真人成年健身引导角色、同一暖棕线稿和粉彩质感；三个封面仍需在媒介与构图上明显不同",
              "palette_hint":str(style.get("palette") or "cream, warm brown, peach, mint, sky blue"),
              "text_policy":"no_text_in_generated_asset"},
            "cover_directions":directions,"selected_cover_index":selected,
            "selected_cover_asset_id":selected_asset,"asset_tasks":tasks}
