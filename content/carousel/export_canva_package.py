"""Export Canva handoff files and a portable layered PPTX."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from content.carousel.pptx_builder import ensure_editable_pptx


def export(post_id: str, outline: dict, visual_spec: dict, asset_plan: dict, rendered_paths: list[str], out_root: str | Path,
           source_dir: str | Path | None = None, cover_directions: list[dict] | None = None,
           image_generation: dict | None = None, cover_variants: list[dict] | None = None) -> str:
    folder = Path(out_root) / post_id; folder.mkdir(parents=True, exist_ok=True)
    pages = []
    for page, visual, rendered in zip(outline["pages"], visual_spec["pages"], rendered_paths):
        copied = folder / Path(rendered).name; shutil.copy2(rendered, copied)
        pages.append({"page_no":page["page_no"],"title":page["title"],"subtitle":page.get("subtitle", ""),
                      "bullets":page.get("bullets",[]),"density":page.get("density","medium"),
                      "info_unit_count":page.get("info_unit_count",0),"modules":page.get("modules",[]),
                      "template_type":visual["template_type"],"rendered_path":copied.name,
                      "canva_notes":"完成页可直接导入；如需修改，按 editable_text_layers.json 覆盖中文并替换 source_assets 中的无字素材。"})
    source = Path(source_dir) if source_dir else None
    assets_dir = folder / "source_assets"
    copied_assets = []
    if source:
        for task in asset_plan.get("asset_tasks", []):
            candidate = source / str(task.get("output_path") or "")
            if candidate.is_file():
                assets_dir.mkdir(exist_ok=True)
                destination = assets_dir / candidate.name
                shutil.copy2(candidate, destination)
                copied_assets.append({"asset_id": task.get("asset_id"), "path": f"source_assets/{destination.name}", "page_no": task.get("page_no")})
    copied_covers=[]
    for item in (cover_variants or []):
        candidate=Path(str(item.get("path") or ""))
        if candidate.is_file():
            destination=folder/candidate.name
            shutil.copy2(candidate,destination)
            copied_covers.append({**{k:v for k,v in item.items() if k!="path"},"path":destination.name})
    manifest = {"post_id": post_id, "pages": pages, "asset_tasks": asset_plan["asset_tasks"], "source_assets": copied_assets,
                "cover_directions": cover_directions or [], "cover_variants":copied_covers,
                "selected_cover_index":asset_plan.get("selected_cover_index",0),
                "image_generation": image_generation or {}}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    editable={"canvas":{"width":1080,"height":1440},"text_policy":"Chinese text is rendered outside image generation",
              "pages":[{"page_no":page["page_no"],"title":page["title"],"subtitle":page.get("subtitle",""),
                        "badge":next((source.get("badge","") for source in outline["pages"] if source.get("page_no")==page["page_no"]),""),
                        "modules":page.get("modules",[])} for page in pages]}
    (folder / "editable_text_layers.json").write_text(json.dumps(editable,ensure_ascii=False,indent=2),encoding="utf-8")
    # Build the editable source during every operator-triggered carousel run.
    # Text, shapes and images remain separate objects when Canva imports it.
    ensure_editable_pptx(folder, force=True)
    return str(folder)
