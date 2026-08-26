#!/usr/bin/env python3
"""小红书封面 V2：结构化 Cover Brief + HTML/CSS 模板 + Playwright 渲染。

主路径：Director -> Critic -> 3个候选模板 -> Playwright screenshot -> Pillow后处理。
失败兜底：任何浏览器/Jinja问题都会退回 Pillow 简版，保证发布链不被封面阻断。
可选插件：rembg（人物/器械素材抠图接口，未安装不影响主流程）。
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from content.cover_system.director import build_brief
from content.cover_system.critic import critique
from content.cover_system.renderer import render_candidates

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "content" / "drafts"
COVERS = ROOT / "content" / "covers"
W, H = 1080, 1440

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF]+")


def _font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _fallback_pillow(text: str, out_path: Path) -> Path:
    """浏览器渲染不可用时保证还能出图。"""
    img = Image.new("RGB", (W, H), (245, 242, 233))
    draw = ImageDraw.Draw(img)
    text = _EMOJI.sub("", text or "封面").strip()[:28]
    draw.rounded_rectangle((70, 70, W - 70, H - 70), radius=52, outline=(20, 20, 20), width=5)
    draw.ellipse((720, 90, 1140, 510), fill=(255, 103, 72))
    title_font = _font(88, True)
    small = _font(32, False)
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=title_font) <= 820:
            cur += ch
        else:
            lines.append(cur); cur = ch
    if cur: lines.append(cur)
    y = 430
    for line in lines[:4]:
        draw.text((110, y), line, fill=(20, 20, 20), font=title_font)
        y += 120
    draw.text((110, H - 155), "FITNESS · HEALTH · DAILY GUIDE", fill=(70, 70, 70), font=small)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92)
    return out_path


def make_cover_candidates(cover_text: str, category: str, out_path: Path, title: str = "") -> dict:
    brief = build_brief(cover_text, category, title=title)
    review = critique(brief)
    try:
        candidates = render_candidates(brief, out_path)
        return {"candidates": candidates, "brief": brief, "critic": review, "fallback": False}
    except Exception as e:
        fallback = out_path.with_name(f"{out_path.stem}_fallback.jpg")
        _fallback_pillow(brief.get("primary_text") or cover_text or title, fallback)
        return {
            "candidates": [{"style": "fallback", "label": "兼容模式", "path": str(fallback).replace("\\", "/")}],
            "brief": brief,
            "critic": {**review, "issues": review.get("issues", []) + [f"HTML渲染失败，已回退Pillow: {e}"]},
            "fallback": True,
        }


def make_cover(cover_text: str, category: str, out_path: Path, title: str = ""):
    """向后兼容旧调用：返回首选候选封面路径。"""
    result = make_cover_candidates(cover_text, category, out_path, title=title)
    return Path(result["candidates"][0]["path"])


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", help="指定草稿json(默认处理所有无图草稿)")
    args = ap.parse_args()

    files = [args.draft] if args.draft else glob.glob(str(DRAFTS / "*.json"))
    done = 0
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        if d.get("images"):
            continue
        cover_text = (d.get("cover_text") or d.get("title") or "").strip()
        if not cover_text:
            continue
        out = COVERS / (Path(f).stem + ".jpg")
        result = make_cover_candidates(cover_text, d.get("category", "_default"), out, title=d.get("title", ""))
        d["cover_candidates"] = result["candidates"]
        d["cover_brief"] = result["brief"]
        d["images"] = result["candidates"][0]["path"]
        json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  ✓ 生成 {len(result['candidates'])} 个封面候选: {Path(f).name}")
        done += 1
    print(f"[封面完成] 处理 {done} 篇")


if __name__ == "__main__":
    main()
