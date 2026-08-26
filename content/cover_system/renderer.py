from __future__ import annotations

import os
import platform
import shutil
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
W, H = 1080, 1440

ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    autoescape=select_autoescape(["html", "xml"]),
)

STYLE_LABELS = {
    "editorial": "极简杂志",
    "question": "反差提问",
    "steps": "数字清单",
}


def _launch_browser(playwright):
    # Windows 用户通常已经装 Edge，优先复用，不要求额外下载 Chromium。
    if platform.system().lower().startswith("win"):
        try:
            return playwright.chromium.launch(channel="msedge", headless=True)
        except Exception:
            pass

    # Playwright no longer ships every Chromium revision for older macOS ARM64
    # releases. Reuse an installed macOS browser before trying the bundled one.
    if platform.system().lower() == "darwin":
        mac_browsers = [
            os.environ.get("XHS_BROWSER_EXECUTABLE", ""),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            str(Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            str(Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
        for raw in mac_browsers:
            exe = Path(raw).expanduser() if raw else None
            if exe and exe.is_file():
                try:
                    return playwright.chromium.launch(executable_path=str(exe), headless=True)
                except Exception:
                    pass

    # CI/Linux 或已安装系统浏览器时，优先直接复用系统 Chromium/Chrome。
    for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
        exe = shutil.which(name)
        if exe:
            try:
                return playwright.chromium.launch(executable_path=exe, headless=True)
            except Exception:
                pass

    # 最后才尝试 Playwright 自带浏览器；若未安装，上层会自动回退 Pillow。
    return playwright.chromium.launch(headless=True)


def _postprocess(path: Path) -> None:
    """Pillow 插件层：轻微锐化/对比度，避免截图文字发灰。"""
    img = Image.open(path).convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(1.03)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=3))
    img.save(path, "JPEG", quality=94, subsampling=0)


def render_one(brief: dict, style: str, out_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    tpl = ENV.get_template(f"{style}.html")
    html = tpl.render(**brief, style=style)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.set_content(html, wait_until="load")
            # HTML/CSS 精确排版后直接截图；先 PNG 再高质量 JPEG。
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)  # close handle so Playwright can write on Windows
            tmp_png = Path(tmp_path)
            try:
                page.screenshot(path=str(tmp_png), full_page=False)
                Image.open(tmp_png).convert("RGB").save(out_path, "JPEG", quality=94, subsampling=0)
            finally:
                tmp_png.unlink(missing_ok=True)
        finally:
            browser.close()
    _postprocess(out_path)
    return out_path


def render_candidates(brief: dict, out_base: Path) -> list[dict]:
    styles = [brief.get("recommended_template", "editorial"), "editorial", "question", "steps"]
    unique = []
    for s in styles:
        if s not in unique:
            unique.append(s)
    unique = unique[:3]

    results = []
    for style in unique:
        path = out_base.with_name(f"{out_base.stem}_{style}.jpg")
        render_one(brief, style, path)
        results.append({"style": style, "label": STYLE_LABELS.get(style, style), "path": str(path).replace("\\", "/")})
    return results


def optional_remove_background(input_path: str, output_path: str) -> bool:
    """可选插件接口：安装 rembg 后可给人物/器械素材抠图；未安装不会影响主流程。"""
    try:
        from rembg import remove
    except Exception:
        return False
    src = Path(input_path).read_bytes()
    Path(output_path).write_bytes(remove(src))
    return True
