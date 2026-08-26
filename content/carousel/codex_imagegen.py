"""Native Codex-CLI image generation for carousel visual assets.

This adapter intentionally calls the local ``codex`` executable rather than an
API.  It is opt-in: callers must set ``image_generation.enabled`` to true.
Every generated asset is no-text; the renderer owns Chinese typography.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image

DEFAULT_TIMEOUT_SECONDS = 600


def _compact(value: object, limit: int = 1800) -> str:
    return " ".join(str(value or "").split())[:limit]


def _cli_path(options: dict | None=None) -> str:
    return str((options or {}).get("codex_cli_path") or os.environ.get("CODEX_CLI_PATH") or "codex")


def _is_available(command: str) -> bool:
    return bool(Path(command).is_file() if "/" in command else shutil.which(command))


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _worker_prompt(task: dict, image_path: Path, result_path: Path) -> str:
    return f"""Generate exactly one PNG image with the native `image_gen.imagegen` tool.

Use case: a no-text visual asset in a Chinese Xiaohongshu fitness carousel.
Image prompt: {_compact(task.get('prompt'))}
Negative prompt: {_compact(task.get('negative_prompt'))}

Rules:
- Use the native image generation tool. Do not call a direct image API.
- Do not make placeholder art with SVG, Python, PIL, HTML, screenshots or code-native drawing.
- Never put text, Chinese characters, English words, letters, numbers, logo or watermark in the image.
- When a person is useful, generate only a fully clothed, non-identifiable adult illustration or mascot; never imitate a real person or celebrity.
- Do not generate minors, revealing body-focused imagery, medical claims, body measurements, or before-and-after comparisons.
- Generate one 3:4 vertical bitmap only.
- Do not modify source files. You may write only the two result files below.

Save the image exactly as a PNG at: {image_path}
Then write this minimal JSON at: {result_path}
{{"status":"complete","image_path":"{image_path}","error":null}}

If native image generation is unavailable, write the same JSON with
`status` set to `failed`, `image_path` set to null, and a concise `error`.
Do not ask the user questions."""


def _valid_png(path: Path) -> tuple[bool, str]:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                return False, "worker output is not a PNG"
            if image.width < 400 or image.height < 400:
                return False, "worker output is unexpectedly small"
        return True, ""
    except Exception:
        return False, "worker did not write a readable PNG"


def _run_one(task: dict, project_root: Path, asset_root: Path, timeout: int,
             reasoning_effort: str="low", model: str="", codex_cli_path: str="") -> dict:
    output_rel = str(task.get("output_path") or "")
    image_path = (asset_root / output_rel).resolve()
    if not output_rel or not _inside(image_path, asset_root):
        return {"asset_id": task.get("asset_id"), "status": "failed", "error": "unsafe asset output path"}
    image_path.parent.mkdir(parents=True, exist_ok=True)
    result_path = image_path.with_suffix(".result.json")
    last_message = image_path.with_suffix(".last-message.txt")
    command = codex_cli_path or _cli_path()
    if not _is_available(command):
        return {"asset_id": task.get("asset_id"), "status": "failed", "error": "Codex CLI is not available on PATH"}

    args = [
        command, "exec", "-C", str(project_root), "--skip-git-repo-check", "--sandbox", "workspace-write",
        "-c", 'approval_policy="on-request"', "-c", f'model_reasoning_effort="{reasoning_effort}"',
        "--ephemeral", "--output-last-message", str(last_message),
    ]
    if model:
        args.extend(["--model", model])
    args.append(_worker_prompt(task, image_path, result_path))
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args, cwd=project_root, text=True, capture_output=True, timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"}, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"asset_id": task.get("asset_id"), "status": "failed", "error": f"Codex CLI timed out after {timeout}s"}
    except OSError as exc:
        return {"asset_id": task.get("asset_id"), "status": "failed", "error": f"Could not start Codex CLI: {exc}"}

    duration_ms = round((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        details = _compact(completed.stderr or completed.stdout, 280)
        return {"asset_id": task.get("asset_id"), "status": "failed", "duration_ms": duration_ms,
                "error": f"Codex CLI exited with {completed.returncode}{': ' + details if details else ''}"}

    worker_error = ""
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("status") != "complete":
                worker_error = _compact(result.get("error") or "imagegen worker reported failure", 280)
        except (OSError, json.JSONDecodeError):
            worker_error = "imagegen worker result was not valid JSON"
    valid, validation_error = _valid_png(image_path)
    if worker_error or not valid:
        return {"asset_id": task.get("asset_id"), "status": "failed", "duration_ms": duration_ms,
                "error": worker_error or validation_error}
    return {"asset_id": task.get("asset_id"), "status": "generated", "duration_ms": duration_ms,
            "output_path": str(image_path.relative_to(asset_root)),
            "command": "codex exec [native imagegen worker]"}


def generate_assets(asset_plan: dict, project_root: str | Path, asset_root: str | Path, options: dict | None = None) -> dict:
    """Generate a bounded set of planned assets and annotate the task plan."""
    opts = options or {}
    enabled = bool(opts.get("enabled", False))
    # Generate all required tasks by default. The old fixed value of six used
    # three slots for covers and silently deferred the last content page.
    raw_limit=opts.get("max_assets")
    limit=int(raw_limit) if raw_limit not in (None,"") else len(asset_plan.get("asset_tasks",[]))
    timeout = max(60, int(opts.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS))
    reasoning=str(opts.get("reasoning_effort") or "low").lower()
    if reasoning not in {"minimal","low","medium","high","xhigh"}: reasoning="low"
    model=str(opts.get("model") or "").strip()
    codex_cli_path=_cli_path(opts)
    tasks = list(asset_plan.get("asset_tasks", []))
    ordered = sorted(tasks, key=lambda item: (item.get("page_no") != 1, item.get("page_no", 999)))
    if not enabled:
        for task in tasks:
            task["status"] = "skipped"
        return {"provider": "codex_cli", "enabled": False, "requested": 0, "generated": 0, "failed": 0,
                "results": [], "note": "Image generation is opt-in. Existing local placeholders were used."}

    results = []
    for index, task in enumerate(ordered):
        if limit and index >= limit:
            task["status"] = "deferred"
            task["generation_metadata"] = {"provider": "codex_cli", "reason": "max_assets limit"}
            continue
        result = _run_one(task, Path(project_root), Path(asset_root), timeout,reasoning,model,codex_cli_path)
        task["status"] = result["status"]
        task["generation_metadata"] = {"provider": "codex_cli", **{key: value for key, value in result.items() if key != "asset_id"}}
        results.append(result)
    return {
        "provider": "codex_cli", "enabled": True, "requested": len(results),
        "generated": sum(item["status"] == "generated" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "deferred": sum(item.get("status") == "deferred" for item in tasks),
        "results": results,
        "note": "Generated assets are no-text. Chinese typography is rendered locally before Canva handoff.",
    }
