"""Normalize operator-owned DOCX/Pages articles into the local voice knowledge base."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_ops import repository


def _clean(text: str) -> str:
    paragraphs = []
    for raw in re.split(r"\r?\n", text or ""):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs).strip() + "\n"


def _read_docx(path: Path) -> str:
    result = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError((result.stderr or "DOCX 文本提取失败").strip()[:300])
    return _clean(result.stdout)


def _read_pages(path: Path) -> str:
    try:
        from pages import Document
    except ImportError as exc:
        raise RuntimeError("读取 .pages 需要临时 python-pages 解析器；请用 --pages-src 指向其 src 目录") from exc
    document = Document(str(path))
    return _clean("\n".join(paragraph.text for paragraph in document.paragraphs))


def extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pages":
        return _read_pages(path)
    raise RuntimeError(f"不支持的写作样本格式: {suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import owned articles as voice-only memory")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--pages-src", help="optional python-pages checkout src directory")
    args = parser.parse_args()
    if args.pages_src:
        sys.path.insert(0, str(Path(args.pages_src).expanduser().resolve()))

    output_dir = ROOT / "data" / "personal_knowledge" / "documents"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for raw_path in args.files:
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        content = extract(source)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        material_id = "voice_" + digest[:12]
        safe_stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", source.stem).strip("_") or material_id
        normalized = output_dir / f"{safe_stem}.txt"
        normalized.write_text(content, encoding="utf-8")
        relative_path = str(normalized.relative_to(ROOT))
        item = repository.upsert_material({
            "material_id": material_id,
            "kind": "writing_sample",
            "title": source.stem,
            "local_path": relative_path,
            "notes": "用户确认的本人原创文章；仅学习语气、论证节奏、证据解释和收束方式，不作为健身事实、热点或本人亲历依据。",
            "tags": ["本人原创", "语气样本", "论证结构", "voice_only"],
            "rights_status": "owned",
            "purpose": "voice_and_argumentation_style_only",
            "content_hash": digest,
            "content_text": content,
            "active": True,
        })
        manifest_rows.append({
            "material_id": item["material_id"], "title": source.stem,
            "source_filename": source.name, "normalized_path": relative_path,
            "characters": len(content), "sha256": digest,
            "purpose": "voice_and_argumentation_style_only", "rights_status": "owned",
        })

    manifest = {
        "schema_version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "privacy": "local_only; source absolute paths are not stored",
        "documents": manifest_rows,
    }
    manifest_path = ROOT / "data" / "personal_knowledge" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "imported": len(manifest_rows), "manifest": str(manifest_path),
                      "material_ids": [row["material_id"] for row in manifest_rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
