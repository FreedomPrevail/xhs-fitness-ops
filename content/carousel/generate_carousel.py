#!/usr/bin/env python3
"""End-to-end local carousel generator."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents import carousel_review_agent, outline_agent, visual_director_agent
from content.carousel import asset_planner, codex_imagegen, cover_directions, export_canva_package, render_post
from golden.llm import agent_config, provider_name

def generate(topic: dict, content: dict, persona: dict | None = None, model_config: dict | None = None,
             personal_materials: list[dict] | None = None) -> dict:
    """Generate a carousel; images are generated only when explicitly enabled."""
    config = model_config or {}
    post_id = datetime.now().strftime("xhs_%Y%m%d_%H%M%S")
    outline = outline_agent.build(topic, content, config, persona=persona, personal_materials=personal_materials)
    visual_spec = visual_director_agent.build(outline, persona)
    directions = cover_directions.build(outline, visual_spec, config, novelty_seed=post_id)
    selected_direction = cover_directions.select(directions, config.get("cover_direction_index", 0))
    assets = asset_planner.build(post_id, visual_spec, directions, int(config.get("cover_direction_index",0) or 0))
    work = ROOT / "content" / "post_pages" / post_id
    image_options=dict(config.get("image_generation") or {})
    per_agent=config.get("agent_reasoning") if isinstance(config.get("agent_reasoning"),dict) else {}
    image_options.setdefault("reasoning_effort",per_agent.get("image") or "low")
    image_config=agent_config(config,"image")
    if provider_name(config,"image")=="codex_cli":
        image_options.setdefault("model",image_config.get("model") or "")
        image_options.setdefault("codex_cli_path",image_config.get("codex_cli_path") or "")
    image_generation = codex_imagegen.generate_assets(assets, ROOT, work, image_options)
    images = render_post.render(outline, visual_spec, assets, work)
    cover_variants = render_post.render_cover_variants(outline, visual_spec, assets, work)
    review = carousel_review_agent.review(outline, visual_spec, image_generation)
    canva_package = export_canva_package.export(
        post_id, outline, visual_spec, assets, images, ROOT / "content" / "canva_packages",
        source_dir=work, cover_directions=directions, image_generation=image_generation,
        cover_variants=cover_variants,
    )
    package = {"post_id": post_id, "outline": outline, "visual_spec": visual_spec,
               "cover_directions": directions, "selected_cover_direction": selected_direction,
               "asset_plan": assets, "image_generation": image_generation, "rendered_pages": images,
               "rendered_cover_variants":cover_variants,
               "review": review, "canva_package": canva_package}
    (work / "carousel_package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a Canva-ready Xiaohongshu carousel")
    parser.add_argument("--draft", required=True)
    parser.add_argument("--generate-images", action="store_true", help="use the logged-in local Codex CLI")
    parser.add_argument("--max-image-assets", type=int, default=6, help="maximum Codex images to generate; first three are cover variants")
    parser.add_argument("--cover-direction", type=int, default=0, choices=range(3), help="0-2, see cover_directions in result JSON")
    args = parser.parse_args()
    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    topic = {"id": draft.get("topic_id", "manual"), "label": draft.get("topic_id", "健身")}
    config = {"cover_direction_index": args.cover_direction,
              "image_generation": {"enabled": args.generate_images, "max_assets": args.max_image_assets}}
    print(json.dumps(generate(topic, draft, model_config=config), ensure_ascii=False, indent=2))
