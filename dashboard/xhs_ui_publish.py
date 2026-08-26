"""Xiaohongshu creator-center draft publisher using one stable OpenCLI browser session.

This module intentionally does NOT depend on `browser state` interactive refs.
It targets controls with selector-first `browser find`/interaction commands so
`interactive: 0` is not a blocker.

The browser session defaults to `xhs-ui`, matching the single-account workflow.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from compliance import operation_policy

OPENCLI = shutil.which("opencli") or "opencli"
SESSION = os.environ.get("XHS_UI_SESSION", "xhs-ui")
PUBLISH_URL = (
    "https://creator.xiaohongshu.com/"
    "publish/publish?from=menu_left&target=image"
)

IMAGE_SELECTORS = [
    'input[type="file"][accept*="image"]',
    'input[type="file"][accept*=".jpg"]',
    'input[type="file"][accept*=".jpeg"]',
    'input[type="file"][accept*=".png"]',
    'input[type="file"]',
]

TITLE_SELECTORS = [
    '[contenteditable="true"][placeholder*="标题"]',
    '[contenteditable="true"][placeholder*="赞"]',
    'input[placeholder*="标题"]',
    'input[placeholder*="title" i]',
    '[contenteditable="true"][class*="title"]',
    'input[maxlength="20"]',
    '.title-input input',
    '.note-title input',
]

BODY_SELECTORS = [
    '[contenteditable="true"][class*="content"]',
    '[contenteditable="true"][class*="editor"]',
    '[contenteditable="true"][placeholder*="描述"]',
    '[contenteditable="true"][placeholder*="正文"]',
    '[contenteditable="true"][placeholder*="内容"]',
    '.note-content [contenteditable="true"]',
    '.editor-content [contenteditable="true"]',
    '.tiptap.ProseMirror',
    '[contenteditable="true"]',
]


def _decode_json(text: str) -> Any:
    raw = (text or "").lstrip("\ufeff").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(raw):
            if ch not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(raw[i:])
                return value
            except json.JSONDecodeError:
                continue
    return None



def _decode_console(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    # Node/OpenCLI normally emits UTF-8. Some Windows wrappers/messages may
    # inherit the active code page, so fall back to GB18030 for diagnostics.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

def _run(*args: str, timeout: int = 30, allow_error: bool = False) -> dict:
    cmd = [OPENCLI, "browser", SESSION, *[str(x) for x in args]]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenCLI browser timeout: {' '.join(cmd[2:])}") from exc

    stdout = _decode_console(proc.stdout).strip()
    stderr = _decode_console(proc.stderr).strip()
    payload = _decode_json(stdout)

    result = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "data": payload,
        "cmd": cmd[1:],
    }
    if proc.returncode != 0 and not allow_error:
        detail = stderr or stdout or f"exit {proc.returncode}"
        combined = (stderr + "\n" + stdout).strip()
        if "Extension update available" in combined and "target" in combined:
            raise RuntimeError(
                "OpenCLI Browser Bridge 版本/协议不匹配（输出包含 Extension update + target）。"
                "请先升级 Browser Bridge，再运行 opencli doctor；原始错误: " + detail[:300]
            )
        raise RuntimeError(detail[:600])
    return result


def _unwrap(value: Any) -> Any:
    """Unwrap common OpenCLI browser envelope shapes."""
    cur = value
    for _ in range(5):
        if not isinstance(cur, dict):
            break
        moved = False
        for key in ("data", "result", "items", "matches"):
            nxt = cur.get(key)
            if nxt is not None and nxt is not cur:
                cur = nxt
                moved = True
                break
        if not moved:
            break
    return cur


def _find_rows(selector: str) -> list[dict]:
    res = _run("find", "--css", selector, "--limit", "20", timeout=15, allow_error=True)
    if not res["ok"]:
        return []
    payload = res.get("data")
    candidates: list[Any] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        for key in ("matches", "items", "results", "data", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates and all(k in payload for k in ("ref", "tag")):
            candidates = [payload]
    return [x for x in candidates if isinstance(x, dict)]


def _pick_target(selectors: list[str], field_name: str) -> tuple[str, dict]:
    """Return a numeric ref when possible, otherwise a unique CSS selector."""
    debug = []
    for selector in selectors:
        rows = _find_rows(selector)
        if not rows:
            debug.append({"selector": selector, "matches": 0})
            continue
        visible = [r for r in rows if r.get("visible") is not False]
        chosen = (visible or rows)[0]
        ref = chosen.get("ref")
        debug.append({"selector": selector, "matches": len(rows), "chosen_ref": ref})
        if ref not in (None, ""):
            return str(ref), chosen
        # If find says there is only one match, CSS is safe as a write target.
        if len(rows) == 1:
            return selector, chosen
    raise RuntimeError(f"找不到{field_name}控件: {json.dumps(debug, ensure_ascii=False)[:600]}")


def _extract_value(result: dict) -> str:
    payload = result.get("data")
    if isinstance(payload, dict):
        if "value" in payload:
            value = payload.get("value")
            return "" if value is None else str(value)
        nested = _unwrap(payload)
        if isinstance(nested, dict) and "value" in nested:
            value = nested.get("value")
            return "" if value is None else str(value)
    raw = result.get("stdout") or ""
    parsed = _decode_json(raw)
    if isinstance(parsed, dict) and "value" in parsed:
        return str(parsed.get("value") or "")
    return raw.strip().strip('"')


def _read_target(target: str, meta: dict) -> str:
    tag = str(meta.get("tag") or "").lower()
    attrs = meta.get("attrs") or {}
    contenteditable = str(attrs.get("contenteditable") or "").lower() == "true"
    mode = "text" if contenteditable or tag not in {"input", "textarea"} else "value"
    res = _run("get", mode, target, timeout=15, allow_error=True)
    if not res["ok"] and mode == "text":
        res = _run("get", "value", target, timeout=15, allow_error=True)
    return _extract_value(res)


def _normalize_text(value: str) -> str:
    return " ".join((value or "").replace("\u200b", "").split())


def _write_title(title: str) -> dict:
    target, meta = _pick_target(TITLE_SELECTORS, "标题")

    # Clear with real keyboard primitives first, then type. This is friendlier to
    # React-controlled fields than directly assigning el.value in page JS.
    _run("focus", target, timeout=15)
    _run("keys", "Control+a", timeout=15)
    _run("keys", "Backspace", timeout=15)
    _run("type", target, title, timeout=20)
    actual = _read_target(target, meta)

    ok = _normalize_text(actual) == _normalize_text(title)
    if not ok:
        # One retry using fill (which OpenCLI verifies) for UI variants where type
        # is swallowed by a controlled input re-render.
        fill = _run("fill", target, title, timeout=20, allow_error=True)
        actual = _read_target(target, meta)
        ok = _normalize_text(actual) == _normalize_text(title)
        if not ok:
            raise RuntimeError(
                f"标题写入后校验失败: expected={title!r}, actual={actual!r}, "
                f"type={meta.get('tag')}, fill_ok={fill.get('ok')}"
            )
    return {"target": target, "actual": actual}


def _write_body(body: str) -> dict:
    target, meta = _pick_target(BODY_SELECTORS, "正文")

    # XHS uses a ProseMirror-based contenteditable editor.  Neither `fill` nor
    # `type` is reliable for long text — `fill` sets textContent which ProseMirror
    # overwrites with its internal state, and `type` emits thousands of keystrokes.
    #
    # `execCommand('insertText')` is the only code path that ProseMirror handles
    # natively (it synthesises a beforeinput event the editor understands).
    # The command string is JSON-escaped so non-ASCII / emoji / quotes survive the
    # Windows subprocess argument encoding unchanged.
    import json as _json
    body_js = _json.dumps(body)  # produces a safe JS string literal

    script = (
        "(()=>{var b=" + body_js + ";"
        # Find body editor — must be contenteditable but NOT the title field.
        # Title has placeholder containing \u6807\u9898 (标题); body has \u6b63\u6587
        # or \u63cf\u8ff0 or \u5185\u5bb9 or no placeholder at all (long-form editor).
        "var eds=[...document.querySelectorAll('[contenteditable=\"true\"]')];"
        "var ed=eds.find(function(e){var ph=(e.getAttribute('placeholder')||'');"
        "return !ph.includes('\\u6807\\u9898')&&!ph.includes('\\u8d5e')});"
        "if(!ed&&eds.length>0)ed=eds[eds.length-1];"  # last contenteditable = body
        "if(!ed)return JSON.stringify({ok:false,error:'no editor'});"
        "ed.focus();"
        "try{document.execCommand('selectAll',false,null);"
        "var ok=document.execCommand('insertText',false,b);"
        "if(ok)return JSON.stringify({ok:true,method:'execCommand',len:b.length})}catch(e){}"
        # Fallback: direct textContent + input event (ProseMirror may ignore)
        "ed.textContent=b;"
        "ed.dispatchEvent(new InputEvent('input',{bubbles:true,cancelable:true}));"
        "return JSON.stringify({ok:true,method:'textContent',len:b.length})})()"
    )
    _run("eval", script, timeout=35, allow_error=True)
    time.sleep(0.5)

    actual = _read_target(target, meta)
    expected_norm = _normalize_text(body)
    actual_norm = _normalize_text(actual)
    # ProseMirror may normalise whitespace / emoji / line-breaks slightly
    # differently from our raw text.  A short exact-prefix plus length threshold
    # (≥ 80 %) keeps false-negatives low while still catching true truncation.
    ok = (
        actual_norm == expected_norm
        or (expected_norm and actual_norm
            and actual_norm[:30] == expected_norm[:30]
            and len(actual_norm) >= max(20, int(len(expected_norm) * 0.8)))
    )
    if not ok:
        raise RuntimeError(
            f"正文写入后校验失败: expected_len={len(expected_norm)}, "
            f"actual_len={len(actual_norm)}, actual_head={actual[:120]!r}"
        )
    return {"target": target, "actual_len": len(actual_norm)}


def _inspect_publish_surface() -> dict:
    """Inspect whether XHS is on image upload/editor or the video surface."""
    # NOTE: Chinese characters in eval scripts must use \\uXXXX escapes
    # because Windows subprocess argument encoding (GBK) corrupts raw CJK.
    script = (
        "(()=>{const t=(document.body&&(document.body.innerText||document.body.textContent))||'';"
        "const ins=[...document.querySelectorAll('input[type=\"file\"]')];"
        "const imgs=ins.filter(e=>{const a=(e.getAttribute('accept')||'').toLowerCase();"
        "return a.includes('image')||a.includes('.jpg')||a.includes('.jpeg')||"
        "a.includes('.png')||a.includes('.gif')||a.includes('.webp')});"
        "const ti=[...document.querySelectorAll('input,textarea,[contenteditable=\"true\"]')].find(e=>{"
        "if(!e||e.offsetParent===null)return false;"
        "const ph=(e.getAttribute('placeholder')||'').trim();"
        "const cl=String(e.className||'');"
        "const ml=Number(e.getAttribute('maxlength')||0);"
        "return ph.includes('\\u6807\\u9898')||/title/i.test(ph)||/title/i.test(cl)||ml===20});"
        "const vd=t.includes('\\u62d6\\u62fd\\u89c6\\u9891\\u5230\\u6b64\\u5904\\u70b9\\u51fb\\u4e0a\\u4f20')||t.includes('\\u4e0a\\u4f20\\u89c6\\u9891');"
        "return{has_image_input:imgs.length>0,image_input_count:imgs.length,has_title_input:!!ti,video_surface:vd,url:location.href}})()"
    )
    res = _run("eval", script, timeout=15, allow_error=True)
    data = _unwrap(res.get("data"))
    if isinstance(data, dict):
        return data
    parsed = _unwrap(_decode_json(res.get("stdout") or ""))
    return parsed if isinstance(parsed, dict) else {}


def _select_image_text_surface() -> dict:
    """Select XHS \u56fe\u6587 / \u4e0a\u4f20\u56fe\u6587 tab before querying the file input."""
    before = _inspect_publish_surface()
    if before.get("has_image_input") or before.get("has_title_input"):
        return {"selected": False, "reason": "already_image_surface", "surface": before}

    # XHS creator center renders the surface switcher as a row of styled
    # <div> / <span> elements (not proper <button> or [role="tab"]).  A
    # single-pass text match on the trimmed innerText is the most robust.
    click_script = (
        "(()=>{const tgt=document.querySelector('input[type=\"file\"]');"
        "if(tgt)return{ok:true,already:true};"
        "var nds=[...document.querySelectorAll('div,span,button,li,a,[role=\"tab\"]')];"
        "for(var i=0;i<nds.length;i++){var n=nds[i];"
        "if(!n.offsetParent||n.getBoundingClientRect().width===0)continue;"
        "var t=(n.innerText||n.textContent||'').replace(/\\s+/g,' ').trim();"
        "if(t==='\\u4e0a\\u4f20\\u56fe\\u6587'||t==='\\u56fe\\u6587'){n.click();"
        "return{ok:true,tag:n.tagName,text:t}}}"
        "return{ok:false,message:'no_image_text_tab'}})()"
    )
    click = _run("eval", click_script, timeout=15, allow_error=True)
    click_data = _unwrap(click.get("data"))
    if not isinstance(click_data, dict):
        click_data = _unwrap(_decode_json(click.get("stdout") or ""))

    last = before
    for _ in range(12):  # up to ~6 seconds
        time.sleep(0.5)
        last = _inspect_publish_surface()
        if last.get("has_image_input") or last.get("has_title_input"):
            return {"selected": True, "click": click_data, "surface": last}

    raise RuntimeError(
        "已进入小红书发布页，但没有切换到图文上传面。"
        f" click={json.dumps(click_data, ensure_ascii=False)[:400]}, "
        f" surface={json.dumps(last, ensure_ascii=False)[:300]}"
    )


def _upload_image(image_path: str) -> dict:
    path = str(Path(image_path).resolve())
    if not Path(path).exists():
        raise RuntimeError(f"封面图不存在: {path}")
    target, meta = _pick_target(IMAGE_SELECTORS, "图片上传")
    res = _run("upload", target, path, timeout=35)
    # Let XHS finish image processing before title/body are located.
    time.sleep(3)
    return {"target": target, "file": path, "meta": meta, "response": res.get("data")}


def _save_draft() -> dict:
    """Trigger XHS draft save inside the same xhs-ui page.

    Current XHS creator center wraps the bottom action in <xhs-publish-btn> with
    a closed shadow root. The host exposes save callbacks; invoking that callback
    is the same workaround used by current OpenCLI's XHS publisher.
    """
    script = (
        "(()=>{const v=e=>{if(!e||e.offsetParent===null)return false;"
        "const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
        "const mtds=['_onSave','_onSaveDraft','_onDraft'];"
        "const hsts=[...document.querySelectorAll('xhs-publish-btn')].filter(v);"
        "let le='';for(const h of hsts){for(const n of mtds){"
        "if(typeof h[n]!=='function')continue;try{h[n]();"
        "return{ok:true,via:'xhs-publish-btn-method',method:n,hosts:hsts.length}}catch(e){le=String(e&&e.message||e)}}}"
        "const lbs=['\\u6682\\u5b58\\u79bb\\u5f00','\\u5b58\\u8349\\u7a3f','\\u4fdd\\u5b58\\u8349\\u7a3f'];"
        "const bts=[...document.querySelectorAll('button,[role=\"button\"]')];"
        "for(const b of bts){const t=(b.innerText||b.textContent||'').trim();"
        "if(v(b)&&!b.disabled&&lbs.some(x=>t===x||t.includes(x))){b.click();"
        "return{ok:true,via:'light-dom-click',text:t,hosts:hsts.length}}}"
        "return{ok:false,via:'none',hosts:hsts.length,lastError:le}})()"
    )
    res = _run("eval", script, timeout=20)
    payload = res.get("data")
    if not isinstance(payload, dict):
        payload = _decode_json(res.get("stdout") or "")
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"没有触发小红书暂存动作: {payload or res.get('stdout')}")
    return payload


def _verify_saved() -> dict:
    time.sleep(4)
    url_res = _run("get", "url", timeout=15, allow_error=True)
    url = (url_res.get("stdout") or "").strip().strip('"')
    marker_script = (
        "(()=>{const ms=['\\u8349\\u7a3f\\u5df2\\u4fdd\\u5b58','\\u6682\\u5b58\\u6210\\u529f','\\u4fdd\\u5b58\\u6210\\u529f','\\u4fdd\\u5b58\\u4e8e','\\u56fe\\u6587\\u7b14\\u8bb0('];"
        "for(const e of document.querySelectorAll('*')){"
        "if(e.tagName==='STYLE'||e.tagName==='SCRIPT')continue;"
        "const t=(e.innerText||e.textContent||'').trim();"
        "if(t&&t.length<=200&&ms.some(m=>t.includes(m)))return t}"
        "return''})()"
    )
    mark_res = _run("eval", marker_script, timeout=15, allow_error=True)
    marker = ""
    if mark_res.get("ok"):
        data = mark_res.get("data")
        if isinstance(data, str):
            marker = data
        elif data is not None:
            marker = str(data)
        else:
            marker = (mark_res.get("stdout") or "").strip().strip('"')

    navigated = bool(url and "/publish/publish" not in url)
    return {
        "ok": bool(marker or navigated),
        "url": url,
        "marker": marker,
        "navigated": navigated,
    }


def _plain_url(res: dict) -> str:
    raw = (res.get("stdout") or "").strip().strip('"')
    # Some OpenCLI builds may emit an agent envelope instead of plain text.
    data = res.get("data")
    if isinstance(data, str) and data.startswith("http"):
        return data.strip()
    if isinstance(data, dict):
        for key in ("url", "value"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value.strip()
    # Ignore update notices accidentally printed around a plain URL.
    for line in reversed(raw.splitlines()):
        line = line.strip().strip('"')
        if line.startswith("http://") or line.startswith("https://"):
            return line
    return raw


def _tab_rows() -> list[dict]:
    res = _run("tab", "list", timeout=15, allow_error=True)
    if not res.get("ok"):
        return []
    payload = res.get("data")
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("tabs", "items", "results", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    parsed = _decode_json(res.get("stdout") or "")
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    return []


def _ensure_publish_page() -> dict:
    """Navigate the xhs-ui session to the XHS image-publish page.

    Uses ``browser open`` (not ``eval`` + ``location.assign``) so navigation
    works reliably from *any* starting page, including ``about:blank``.
    """
    url_res = _run("get", "url", timeout=15, allow_error=True)
    url = _plain_url(url_res) if url_res.get("ok") else ""

    if "creator.xiaohongshu.com" not in url:
        # The session may own more than one tab. Select the existing creator tab
        # by its documented `page` identity rather than binding/opening another.
        rows = _tab_rows()
        creator = next((r for r in rows if "creator.xiaohongshu.com" in str(r.get("url") or "")), None)
        if creator:
            page = creator.get("page")
            if page:
                sel = _run("tab", "select", str(page), timeout=15, allow_error=True)
                if not sel.get("ok"):
                    raise RuntimeError("找到小红书 tab，但无法切换到该 tab: " + (sel.get("stderr") or sel.get("stdout") or ""))
                time.sleep(1)
                url_res = _run("get", "url", timeout=15)
                url = _plain_url(url_res)

    # If we still aren't on the right page, use `browser open` to navigate.
    # This is a browser-level navigation that works from about:blank / any page.
    if "/publish/publish" not in url or "target=image" not in url:
        _run("open", PUBLISH_URL, timeout=25, allow_error=True)
        time.sleep(3)
        url_res = _run("get", "url", timeout=15)
        url = _plain_url(url_res)

    if "/publish/publish" not in url:
        raise RuntimeError(
            "xhs-ui 无法进入小红书发布页。请确认 Browser Bridge 已连接；"
            f"当前 URL: {url or '(empty)'}"
        )

    return {"url": url, "reused_existing_tab": True}


def push_to_xhs_draft(title: str, body: str, image_path: str,
                      topics: list[str] | None = None) -> dict:
    """Push image/title/body into the single XHS creator draft box.

    Topic tags are appended as inline ``#tag`` hashtags at the end of the body
    so they become real in-text topics on Xiaohongshu.

    Returns a structured result and always records which step failed. No call in
    this workflow waits 180 seconds, so Flask tasks should not remain `result:null`
    for minutes when XHS's site adapter gets stuck.
    """
    blocked = operation_policy.blocked_result("platform_publish")
    if blocked:
        return blocked
    # Append inline hashtags to body text
    full_body = body
    if topics:
        tags = " ".join(f"#{t}" for t in topics if t and not t.startswith("#"))
        if tags:
            full_body = body.rstrip() + "\n\n" + tags

    steps: list[dict] = []
    try:
        page_info = _ensure_publish_page()
        steps.append({"step": "open_publish_page", "ok": True, **page_info})
        steps.append({"step": "creator_url", "ok": True, "url": page_info["url"]})

        surface_info = _select_image_text_surface()
        steps.append({"step": "select_image_text", "ok": True, **surface_info})

        upload = _upload_image(image_path)
        steps.append({"step": "upload_image", "ok": True, "target": upload["target"]})

        title_info = _write_title(title)
        steps.append({"step": "write_title", "ok": True, "target": title_info["target"]})

        body_info = _write_body(full_body)
        steps.append({"step": "write_body", "ok": True, "target": body_info["target"],
                      "actual_len": body_info["actual_len"]})

        save_info = _save_draft()
        steps.append({"step": "save_draft", "ok": True, **save_info})

        verification = _verify_saved()
        steps.append({"step": "verify_ui", **verification})

        # Save was explicitly invoked; if XHS does not expose a success marker,
        # report partial confirmation rather than hanging or returning null.
        return {
            "ok": True,
            "session": SESSION,
            "mode": "xhs_ui",
            "image_saved": True,
            "title_saved": True,
            "body_saved": True,
            "save_invoked": True,
            "ui_confirmed": bool(verification.get("ok")),
            "verification": verification,
            "steps": steps,
            "warning": "" if verification.get("ok") else (
                "已执行小红书暂存动作，但页面未出现明确成功标记；请在草稿箱人工确认一次"
            ),
        }
    except Exception as exc:
        steps.append({"step": "failed", "ok": False, "error": str(exc)[:800]})
        return {
            "ok": False,
            "session": SESSION,
            "mode": "xhs_ui",
            "error": str(exc),
            "steps": steps,
        }
