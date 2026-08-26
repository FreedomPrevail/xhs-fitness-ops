"""Golden 封面视觉分析入口：保存真实封面字节后才允许输出视觉 Feature。"""
from __future__ import annotations

import urllib.request
from pathlib import Path

from .llm import call_json, is_configured
from compliance import operation_policy

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "golden_covers"


def _safe_name(candidate_id: str, suffix: str = ".jpg") -> str:
    clean = "".join(c for c in str(candidate_id) if c.isalnum() or c in "_-")[:80]
    return (clean or "cover") + suffix


def download_cover(candidate: dict) -> Path | None:
    raw=candidate.get("raw") if isinstance(candidate.get("raw"),dict) else {}
    local=str(candidate.get("local_cover_path") or raw.get("local_cover_path") or "").strip()
    if local:
        path=Path(local).expanduser()
        try:
            if path.is_file() and path.stat().st_size > 100:
                return path.resolve()
        except OSError:
            pass
    url = str(candidate.get("cover_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if operation_policy.blocked_result("platform_detail_fetch"):
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        suffix = ".jpg"
    path = CACHE / _safe_name(candidate.get("candidate_id", "cover"), suffix)
    if path.exists() and path.stat().st_size > 100:
        return path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(8 * 1024 * 1024 + 1)
        if len(data) <= 100 or len(data) > 8 * 1024 * 1024:
            return None
        path.write_bytes(data)
        return path
    except Exception:
        return None


def analyze_cover(candidate: dict, cfg: dict) -> dict:
    """Analyze a real local cover through HTTP vision or Codex CLI image input."""
    path = download_cover(candidate)
    if not path:
        return {"visual_observed": False, "status": "missing_image",
                "reason": "没有可下载的真实封面图片；未生成伪视觉Feature"}
    if not is_configured(cfg,role="golden_cover"):
        return {"visual_observed": False, "status": "model_not_configured",
                "local_path": str(path), "reason": "封面已保存，但没有配置视觉模型"}
    prompt = """分析这张小红书健身图文封面。只输出JSON。只描述图片中真实可见内容，不推断帖子受众统计。
返回：{"cover_type":"","layout":"","dominant_colors":[],"contrast":"low|medium|high","visual_subject":"","person_present":false,"body_or_action":"","ocr_text":[],"text_length_estimate":0,"visual_focus":"","cover_hook":"","title_cover_alignment":"","learnable_elements":[],"risks":[],"confidence":0.0}"""
    try:
        out = call_json(prompt,cfg,role="golden_cover",images=[path])
        out.update({"visual_observed": True, "status": "analyzed", "local_path": str(path)})
        return out
    except Exception as exc:
        return {"visual_observed": False, "status": "vision_model_failed", "local_path": str(path),
                "reason": str(exc)[:240]}
