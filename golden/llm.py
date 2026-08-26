"""Structured LLM gateway for HTTP-compatible models and the local Codex CLI."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
GENERIC_OBJECT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": True,
}
CODEX_WRAPPER_SCHEMA = {
    "type": "object",
    "properties": {"result_json": {"type": "string"}},
    "required": ["result_json"],
    "additionalProperties": False,
}
ROLE_REASONING = {
    "topic": "high",
    "golden": "high",
    "content": "high",
    "outline": "medium",
    "cover": "medium",
    "golden_cover": "medium",
    "generic": "medium",
}
REASONING_LEVELS = {"minimal", "low", "medium", "high", "xhigh"}


def _extract_json(raw: str) -> dict:
    raw = (raw or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("模型没有返回 JSON")
    value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("模型返回值不是 JSON object")
    return value


def _provider_name(cfg: dict | None = None) -> str:
    cfg = cfg or {}
    explicit = str(cfg.get("provider") or cfg.get("llm_provider") or "").strip().lower()
    if explicit in {"codex", "codex_cli", "codex-cli"}:
        return "codex_cli"
    return "http"


def agent_config(cfg: dict | None = None, role: str = "generic") -> dict:
    """Resolve one agent's provider without leaking the HTTP model into Codex CLI.

    ``agent_providers`` accepts either a provider name or a small override object.
    Provider credentials/models may live in ``providers.http`` and
    ``providers.codex_cli``.  Older single-provider payloads remain valid.
    """
    base = dict(cfg or {})
    routes = base.get("agent_providers") if isinstance(base.get("agent_providers"), dict) else {}
    route = routes.get(role)
    if route is None:
        return base
    override = {"provider": route} if isinstance(route, str) else dict(route or {})
    provider = _provider_name(override if override.get("provider") else base)
    providers = base.get("providers") if isinstance(base.get("providers"), dict) else {}
    provider_values = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
    resolved = {**base, **provider_values, **override, "provider": provider}
    if provider != _provider_name(base) and "model" not in provider_values and "model" not in override:
        resolved.pop("model", None)
    return resolved


def provider_name(cfg: dict | None = None, role: str | None = None) -> str:
    resolved = agent_config(cfg, role) if role else dict(cfg or {})
    return _provider_name(resolved)


def is_configured(cfg: dict | None = None, role: str | None = None) -> bool:
    cfg = agent_config(cfg, role) if role else dict(cfg or {})
    if _provider_name(cfg) == "codex_cli":
        return True
    return bool(str(cfg.get("base_url") or "").strip()
                and str(cfg.get("api_key") or "").strip()
                and str(cfg.get("model") or "").strip())


def load_output_schema(schema: dict | str | Path | None = None) -> dict:
    if isinstance(schema, dict):
        return schema
    if schema:
        path = Path(schema)
        if not path.is_absolute():
            path = SCHEMA_DIR / path
        return json.loads(path.read_text(encoding="utf-8"))
    return dict(GENERIC_OBJECT_SCHEMA)


def _validate_value(value, schema: dict, path: str = "$") -> None:
    """Validate the schema subset used by this project after Codex unwraps it."""
    expected=schema.get("type")
    valid_type=True
    if expected=="object": valid_type=isinstance(value,dict)
    elif expected=="array": valid_type=isinstance(value,list)
    elif expected=="string": valid_type=isinstance(value,str)
    elif expected=="integer": valid_type=isinstance(value,int) and not isinstance(value,bool)
    elif expected=="number": valid_type=isinstance(value,(int,float)) and not isinstance(value,bool)
    elif expected=="boolean": valid_type=isinstance(value,bool)
    if expected and not valid_type:
        raise RuntimeError(f"Codex JSON schema 校验失败: {path} 应为 {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise RuntimeError(f"Codex JSON schema 校验失败: {path} 不在允许值中")
    if "const" in schema and value != schema["const"]:
        raise RuntimeError(f"Codex JSON schema 校验失败: {path} 不符合固定值")
    if isinstance(value,dict):
        for key in schema.get("required",[]):
            if key not in value:
                raise RuntimeError(f"Codex JSON schema 校验失败: {path}.{key} 缺失")
        properties=schema.get("properties") if isinstance(schema.get("properties"),dict) else {}
        for key,item in value.items():
            if key in properties:
                _validate_value(item,properties[key],f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise RuntimeError(f"Codex JSON schema 校验失败: {path}.{key} 不允许")
    if isinstance(value,list):
        if len(value)<int(schema.get("minItems") or 0):
            raise RuntimeError(f"Codex JSON schema 校验失败: {path} 项目过少")
        if schema.get("maxItems") is not None and len(value)>int(schema["maxItems"]):
            raise RuntimeError(f"Codex JSON schema 校验失败: {path} 项目过多")
        item_schema=schema.get("items") if isinstance(schema.get("items"),dict) else None
        if item_schema:
            for index,item in enumerate(value): _validate_value(item,item_schema,f"{path}[{index}]")
    if isinstance(value,str):
        if schema.get("minLength") is not None and len(value)<int(schema["minLength"]):
            raise RuntimeError(f"Codex JSON schema 校验失败: {path} 文本过短")
        if schema.get("maxLength") is not None and len(value)>int(schema["maxLength"]):
            raise RuntimeError(f"Codex JSON schema 校验失败: {path} 文本过长")
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if schema.get("minimum") is not None and value<float(schema["minimum"]):
            raise RuntimeError(f"Codex JSON schema 校验失败: {path} 小于下限")
        if schema.get("maximum") is not None and value>float(schema["maximum"]):
            raise RuntimeError(f"Codex JSON schema 校验失败: {path} 超过上限")


def _reasoning_effort(cfg: dict, role: str) -> str:
    per_agent = cfg.get("agent_reasoning") if isinstance(cfg.get("agent_reasoning"), dict) else {}
    value = str(per_agent.get(role) or cfg.get("reasoning_effort") or ROLE_REASONING.get(role, "medium")).lower()
    return value if value in REASONING_LEVELS else ROLE_REASONING.get(role, "medium")


def _cli_path(cfg: dict) -> str:
    return str(cfg.get("codex_cli_path") or os.environ.get("CODEX_CLI_PATH") or "codex")


def _cli_available(command: str) -> bool:
    return bool(Path(command).is_file() if "/" in command else shutil.which(command))


def _image_content(paths: list[str | Path]) -> list[dict]:
    content = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"图片不存在: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    return content


def _call_http_json(prompt: str, cfg: dict, images: list[str | Path]) -> dict:
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    if not is_configured(cfg):
        raise RuntimeError("HTTP LLM 需要 base_url/api_key/model")
    content: str | list[dict] = prompt
    if images:
        content = [{"type": "text", "text": prompt}, *_image_content(images)]
    payload = {
        "model": cfg.get("model") or "",
        "messages": [{"role": "user", "content": content}],
        "temperature": float(cfg.get("temperature", 0.2)),
    }
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + str(cfg.get("api_key") or "")},
    )
    with urllib.request.urlopen(request, timeout=int(cfg.get("timeout_seconds") or 240)) as response:
        data = json.loads(response.read().decode("utf-8"))
    return _extract_json(data["choices"][0]["message"]["content"])


def _call_codex_json(prompt: str, cfg: dict, schema: dict, role: str,
                     images: list[str | Path]) -> dict:
    command = _cli_path(cfg)
    if not _cli_available(command):
        raise RuntimeError("Codex CLI 不在 PATH；请先安装/登录或配置 CODEX_CLI_PATH")
    timeout = max(60, int(cfg.get("timeout_seconds") or 600))
    effort = _reasoning_effort(cfg, role)
    with tempfile.TemporaryDirectory(prefix="xhs_codex_llm_") as tmp:
        temp = Path(tmp)
        schema_path = temp / "output.schema.json"
        result_path = temp / "result.json"
        schema_path.write_text(json.dumps(CODEX_WRAPPER_SCHEMA, ensure_ascii=False), encoding="utf-8")
        args = [
            command, "exec", "-C", str(ROOT), "--skip-git-repo-check",
            "--sandbox", "read-only", "--color", "never", "--ephemeral",
            "-c", 'approval_policy="never"',
            "-c", f'model_reasoning_effort="{effort}"',
            "--output-schema", str(schema_path),
            "--output-last-message", str(result_path),
        ]
        model = str(cfg.get("model") or "").strip()
        if model:
            args.extend(["--model", model])
        for image in images:
            path = Path(image).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(f"图片不存在: {path}")
            args.extend(["--image", str(path)])
        args.append("-")
        wrapped_prompt=(prompt+"\n\nCodex输出协议：最终响应必须是一个严格JSON对象，只有 result_json 一个字符串字段。"
                        "result_json 的字符串内容必须是符合下面目标schema的JSON object；不要在字符串外增加解释。\n"
                        +json.dumps(schema,ensure_ascii=False,separators=(",",":")))
        completed = subprocess.run(
            args, cwd=ROOT, input=wrapped_prompt, text=True, capture_output=True,
            timeout=timeout, env={**os.environ, "NO_COLOR": "1"}, check=False,
        )
        if completed.returncode != 0:
            detail = re.sub(r"\s+", " ", completed.stderr or completed.stdout or "").strip()[:500]
            raise RuntimeError(f"Codex CLI 退出码 {completed.returncode}" + (f": {detail}" if detail else ""))
        if not result_path.is_file():
            raise RuntimeError("Codex CLI 没有写出结构化结果")
        wrapper=_extract_json(result_path.read_text(encoding="utf-8"))
        raw_result=wrapper.get("result_json")
        if not isinstance(raw_result,str):
            raise RuntimeError("Codex CLI 结构化包装缺少 result_json 字符串")
        result=_extract_json(raw_result)
        _validate_value(result,schema)
        return result


def call_json(prompt: str, cfg: dict | None = None, *, schema: dict | str | Path | None = None,
              role: str = "generic", images: list[str | Path] | None = None) -> dict:
    """Return a JSON object from either the configured HTTP backend or Codex CLI."""
    cfg = agent_config(cfg, role)
    image_paths = list(images or [])
    output_schema = load_output_schema(schema)
    if _provider_name(cfg) == "codex_cli":
        return _call_codex_json(prompt, cfg, output_schema, role, image_paths)
    return _call_http_json(prompt, cfg, image_paths)
