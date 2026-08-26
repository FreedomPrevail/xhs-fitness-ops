"""Portable layered PPTX builder for Canva Design Import.

The generated deck contains native text boxes, shapes and image objects.  The
JPG previews are never used as flattened slide backgrounds.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

CANVAS_W = 1080
CANVAS_H = 1440
SLIDE_W_IN = 7.5
SLIDE_H_IN = 10.0
FONT = "Microsoft YaHei"
COLORS = {
    "bg": "FFF9EE", "paper": "FFFDF8", "ink": "513520", "brown": "8B5B3E",
    "peach": "F6C7AE", "mint": "CFE8D1", "blue": "CDE8F2", "yellow": "F8E4A3",
    "coral": "E98267", "muted": "A77D61", "line": "D9BEA9", "white": "FFFFFF",
}
PASTELS = ["F6C7AE", "CFE8D1", "CDE8F2", "F8E4A3", "E6D7F2", "F4D7DD"]


def _in_x(px: float):
    return Inches(px * SLIDE_W_IN / CANVAS_W)


def _in_y(px: float):
    return Inches(px * SLIDE_H_IN / CANVAS_H)


def _rgb(value: str) -> RGBColor:
    value = value.lstrip("#")
    return RGBColor.from_string(value)


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _font_size(text: str, base: int, minimum: int) -> int:
    length = len(_clean(text))
    if length <= 18:
        return base
    if length <= 34:
        return max(minimum, base - 2)
    return minimum


def _shape(slide, x, y, w, h, fill: str, *, radius=True, line: str | None = None, name: str = ""):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, _in_x(x), _in_y(y), _in_x(w), _in_y(h))
    shape.name = name or shape.name
    shape.fill.solid(); shape.fill.fore_color.rgb = _rgb(fill)
    shape.line.color.rgb = _rgb(line or fill)
    shape.line.width = Pt(1.2)
    return shape


def _text(slide, text: str, x, y, w, h, size: int, *, bold=False, color="ink",
          align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP, name=""):
    box = slide.shapes.add_textbox(_in_x(x), _in_y(y), _in_x(w), _in_y(h))
    box.name = name or box.name
    frame = box.text_frame
    frame.clear(); frame.word_wrap = True
    frame.margin_left = frame.margin_right = _in_x(4)
    frame.margin_top = frame.margin_bottom = _in_y(3)
    frame.vertical_anchor = valign
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    paragraph.space_after = Pt(0)
    run = paragraph.add_run(); run.text = _clean(text)
    run.font.name = FONT; run.font.size = Pt(size); run.font.bold = bold
    run.font.color.rgb = _rgb(COLORS.get(color, color))
    return box


def _background(slide, variant: int):
    background = slide.background.fill
    background.solid(); background.fore_color.rgb = _rgb(COLORS["bg"])
    _shape(slide, 22, 22, 1036, 1396, COLORS["bg"], line="C89E7E", name="page-border")
    _shape(slide, 875, 0, 205, 170, PASTELS[variant % len(PASTELS)], line=PASTELS[variant % len(PASTELS)], name="decor-top-right")


def _footer(slide, page_no: int):
    _shape(slide, 58, 1355, 964, 2, COLORS["line"], radius=False, name="footer-rule")
    _text(slide, "AI 辅助创作 · 发布前核对训练条件与身体反馈", 58, 1370, 770, 38, 13,
          color="brown", valign=MSO_ANCHOR.MIDDLE, name="compliance-footer")
    badge = _shape(slide, 918, 1365, 104, 46, COLORS["brown"], name="page-number-bg")
    badge.text_frame.clear(); badge.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = badge.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = str(page_no).zfill(2); r.font.name = FONT; r.font.size = Pt(15); r.font.bold = True; r.font.color.rgb = _rgb(COLORS["white"])


def _badge(slide, text: str, y=112):
    width = min(390, max(170, 80 + len(_clean(text)) * 31))
    pill = _shape(slide, 56, y, width, 54, COLORS["yellow"], line=COLORS["brown"], name="page-badge")
    pill.text_frame.clear(); pill.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = pill.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = _clean(text or "实用指南"); r.font.name = FONT; r.font.size = Pt(18); r.font.bold = True; r.font.color.rgb = _rgb(COLORS["ink"])


def _asset_path(package_dir: Path, manifest: dict, page_no: int) -> Path | None:
    rows = [row for row in manifest.get("source_assets", []) if int(row.get("page_no") or 0) == page_no]
    if page_no == 1 and rows:
        index = int(manifest.get("selected_cover_index") or 0) + 1
        selected = next((row for row in rows if f"_cover_{index:02d}" in str(row.get("path") or "")), rows[0])
    else:
        selected = rows[0] if rows else None
    if not selected:
        return None
    path = (package_dir / str(selected.get("path") or "")).resolve()
    return path if path.is_file() else None


def _add_picture_contain(slide, path: Path, x, y, w, h, name: str):
    with Image.open(path) as image:
        iw, ih = image.size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    picture = slide.shapes.add_picture(str(path), _in_x(x + (w - pw) / 2), _in_y(y + (h - ph) / 2), _in_x(pw), _in_y(ph))
    picture.name = name
    return picture


def _module_rows(module: dict) -> list[dict]:
    columns = [item for item in (module.get("columns") or []) if isinstance(item, dict)][:2]
    if len(columns) == 2 and module.get("type") in {"comparison", "mistake_fix"}:
        return [{"label": row.get("title"), "detail": "\n".join(map(str, row.get("items") or [])), "tag": "先判断" if i == 0 else "再选择"}
                for i, row in enumerate(columns)]
    return [row for row in (module.get("items") or []) if isinstance(row, dict)][:6]


def _module(slide, module: dict, x, y, w, h, index: int):
    _shape(slide, x, y, w, h, COLORS["paper"], line=COLORS["line"], name=f"module-{index + 1}")
    title = _clean(module.get("title") or "本页重点")
    pill_w = min(w - 44, max(220, 80 + len(title) * 30))
    pill = _shape(slide, x + 20, y + 14, pill_w, 48, PASTELS[index % len(PASTELS)], line=COLORS["brown"], name=f"module-{index + 1}-title-bg")
    pill.text_frame.clear(); pill.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = pill.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = title; r.font.name = FONT; r.font.size = Pt(18); r.font.bold = True; r.font.color.rgb = _rgb(COLORS["ink"])
    rows = _module_rows(module)
    if not rows:
        _text(slide, module.get("note") or "根据身体反馈逐步执行", x + 28, y + 82, w - 56, h - 106, 16,
              color="brown", align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE, name=f"module-{index + 1}-empty")
        return
    columns = 1 if len(rows) == 1 else 2
    lines = (len(rows) + columns - 1) // columns
    gap = 12
    card_w = (w - 40 - gap * (columns - 1)) / columns
    card_h = (h - 92 - gap * (lines - 1)) / lines
    for item_index, row in enumerate(rows):
        col, line = item_index % columns, item_index // columns
        span_last = columns == 2 and len(rows) % 2 == 1 and item_index == len(rows) - 1
        current_w = w - 40 if span_last else card_w
        left, top = x + 20 + (0 if span_last else col * (card_w + gap)), y + 76 + line * (card_h + gap)
        _shape(slide, left, top, current_w, card_h, COLORS["white"], line="E2CDBB", name=f"module-{index + 1}-item-{item_index + 1}")
        compact = card_h < 122
        marker_size = 36 if compact else 40
        marker = _shape(slide, left + 12, top + 10, marker_size, marker_size, PASTELS[(index + item_index) % len(PASTELS)], line=COLORS["brown"], name=f"item-{item_index + 1}-marker")
        marker.text_frame.clear(); marker.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = marker.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
        rr = p.add_run(); rr.text = str(item_index + 1); rr.font.name = FONT; rr.font.size = Pt(12); rr.font.bold = True; rr.font.color.rgb = _rgb(COLORS["ink"])
        label = _clean(row.get("label") or row.get("tag") or f"要点{item_index + 1}")
        detail = " · ".join(filter(None, [_clean(row.get("value")), _clean(row.get("detail") or row.get("cue")), _clean(row.get("tag"))]))
        text_left = left + marker_size + 22
        label_h = 34 if compact else 42
        detail_top = top + (40 if compact else 50)
        detail_size = (10 if len(detail) <= 24 else 9) if compact else _font_size(detail, 14, 11)
        _text(slide, label, text_left, top + 6, current_w - marker_size - 34, label_h, 15 if compact else 16,
              bold=True, valign=MSO_ANCHOR.MIDDLE, name=f"item-{item_index + 1}-label")
        _text(slide, detail or "按身体反馈调整", text_left, detail_top, current_w - marker_size - 34, max(28, card_h - (46 if compact else 58)),
              detail_size, color="brown", name=f"item-{item_index + 1}-detail")


def _cover(prs: Presentation, page: dict, package_dir: Path, manifest: dict):
    slide = prs.slides.add_slide(prs.slide_layouts[6]); _background(slide, 0)
    _text(slide, "FITNESS · GUIDE", 58, 48, 430, 40, 15, bold=True, color="brown", name="cover-kicker")
    _badge(slide, page.get("badge") or "新手指南", 112)
    title = _clean(page.get("title") or "健身指南")
    size = 56 if len(title) <= 10 else 48 if len(title) <= 16 else 40
    _text(slide, title, 56, 192, 968, 190, size, bold=True, name="cover-title")
    _text(slide, page.get("subtitle") or "保存下来照着做", 60, 380, 900, 62, 21, bold=True, color="coral", valign=MSO_ANCHOR.MIDDLE, name="cover-subtitle")
    _shape(slide, 68, 476, 944, 780, COLORS["white"], line=COLORS["line"], name="cover-art-frame")
    art = _asset_path(package_dir, manifest, 1)
    if art:
        _add_picture_contain(slide, art, 86, 494, 908, 744, "cover-image")
    else:
        _text(slide, "可替换无字封面插画", 150, 760, 780, 100, 20, color="muted", align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE, name="cover-image-placeholder")
    _footer(slide, 1)


def _content_page(prs: Presentation, page: dict, package_dir: Path, manifest: dict, variant: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6]); _background(slide, variant)
    _text(slide, "FITNESS · GUIDE", 56, 46, 400, 38, 14, bold=True, color="brown", name="page-kicker")
    _badge(slide, page.get("badge") or "实用指南", 106)
    title = _clean(page.get("title") or "本页重点")
    _text(slide, title, 56, 176, 968, 112, 36 if len(title) <= 14 else 30, bold=True, name="page-title")
    _text(slide, page.get("subtitle") or "", 58, 286, 940, 52, 19, bold=True, color="coral", valign=MSO_ANCHOR.MIDDLE, name="page-subtitle")
    modules = [m for m in (page.get("modules") or []) if isinstance(m, dict)][:3]
    if not modules:
        modules = [{"title": "本页行动", "items": [], "note": "根据身体反馈逐步执行"}]
    art = _asset_path(package_dir, manifest, int(page.get("page_no") or 1))
    modules_top = 370
    if art:
        # Keep the complete illustration visible, but size the frame around a
        # portrait asset instead of leaving a full-width strip of empty space.
        art_height = 250 if len(modules) <= 2 else 176
        art_width = 660 if len(modules) <= 2 else 500
        art_left = (CANVAS_W - art_width) / 2
        _shape(slide, art_left, 356, art_width, art_height, COLORS["white"], line=COLORS["line"], name="page-art-frame")
        _add_picture_contain(slide, art, art_left + 16, 368, art_width - 32, art_height - 24, "page-illustration")
        modules_top = 626 if len(modules) <= 2 else 552
    bottom, gap = 1338, 16
    height = (bottom - modules_top - gap * (len(modules) - 1)) / len(modules)
    for index, module in enumerate(modules):
        _module(slide, module, 58, modules_top + index * (height + gap), 964, height, index)
    _footer(slide, int(page.get("page_no") or variant + 1))


def ensure_editable_pptx(package_dir: str | Path, *, force: bool = True) -> Path:
    package_dir = Path(package_dir).resolve()
    manifest_path = package_dir / "manifest.json"
    layers_path = package_dir / "editable_text_layers.json"
    if not manifest_path.is_file() or not layers_path.is_file():
        raise FileNotFoundError("Canva 包缺少 manifest.json 或 editable_text_layers.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = json.loads(layers_path.read_text(encoding="utf-8"))
    pages = [page for page in layers.get("pages", []) if isinstance(page, dict)]
    if len(pages) < 2:
        raise ValueError("Canva 可编辑包至少需要封面和一张内容页")
    post_id = _clean(manifest.get("post_id") or package_dir.name)
    output = package_dir / f"{post_id}_Canva可编辑.pptx"
    if output.exists() and not force:
        return output
    prs = Presentation(); prs.slide_width = Inches(SLIDE_W_IN); prs.slide_height = Inches(SLIDE_H_IN)
    # python-pptx starts without slides, so no template slide needs removal.
    _cover(prs, pages[0], package_dir, manifest)
    for index, page in enumerate(pages[1:], start=1):
        _content_page(prs, page, package_dir, manifest, index)
    prs.save(output)
    manifest["editable_canva_deck"] = output.name
    manifest["editable_layer_types"] = ["text", "shape", "image"]
    manifest["canva_import_mode"] = "connect_api_pptx"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (package_dir / "CANVA_IMPORT.md").write_text(
        f"""# Canva 可编辑导入步骤

## 自动方式（推荐）

```bash
python -m canva_connect upload --post-id {post_id} --open
```

系统会自动刷新 OAuth 令牌、上传 `{output.name}`、等待 Canva 转换完成并返回编辑链接。

## 人工兜底

也可以在 Canva 首页选择“上传/导入文件”，上传 `{output.name}`。文字、卡片、色块和图片均为独立对象；导入后请检查中文字体、换行、裁切和图片完整性。

发布前仍需人工检查合规与 AI 标识，并通过小红书官方客户端发布。
""", encoding="utf-8")
    return output
