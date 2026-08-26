"""Read-only adapter for Xiaohongshu Creator Center account analytics.

The adapter opens the official account statistics page in the existing xhs-ui
browser session and reads visible DOM text.  It never bypasses login/security
checks and never invents values hidden inside charts.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compliance import operation_policy

RAW = ROOT / "data" / "raw"
OPENCLI = shutil.which("opencli") or "opencli"
SESSION = os.environ.get("XHS_UI_SESSION", "xhs-ui")
ACCOUNT_URL = "https://creator.xiaohongshu.com/statistics/account/v2"
DATA_ANALYSIS_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
FANS_DATA_URL = "https://creator.xiaohongshu.com/statistics/fans-data"
REQUIRED_SECTIONS = ("账号概览", "内容分析", "粉丝数据")
SECTION_URLS = {
    "账号概览": ACCOUNT_URL,
    "内容分析": DATA_ANALYSIS_URL,
    "粉丝数据": FANS_DATA_URL,
}

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NUMBER_RE = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?[万wWkK])"

ACCOUNT_METRICS = {
    "views": ("观看数", "浏览量", "播放量"),
    "impressions": ("曝光数", "展现量"),
    "clicks": ("点击数", "进入数"),
    "likes": ("点赞数", "点赞"),
    "favorites": ("收藏数", "收藏"),
    "comments": ("评论数", "评论"),
    "shares": ("分享数", "分享"),
    "rise_fans": ("新增粉丝", "净增粉丝", "涨粉数", "涨粉"),
    "home_views": ("主页访问量", "主页访问", "主页浏览量"),
    "notes": ("发布笔记数", "笔记数", "发布数"),
}

RATIO_METRICS = {
    "ctr": ("点击率", "进入率", "CTR"),
    "completion_rate": ("完播率", "阅读完成率"),
}

DURATION_METRICS = {
    "avg_view_time_seconds": ("平均观看时长", "平均阅读时长", "平均播放时长"),
}

NOTE_FIELDS = {
    "note_id": ("note_id", "noteid", "笔记id", "笔记ID"),
    "title": ("标题", "笔记标题", "内容标题"),
    "published_at": ("发布时间", "发布日期", "发布于"),
    "views": ("观看数", "浏览量", "播放量"),
    "impressions": ("曝光数", "展现量"),
    "clicks": ("点击数", "进入数"),
    "likes": ("点赞数", "点赞"),
    "favorites": ("收藏数", "收藏"),
    "comments": ("评论数", "评论"),
    "shares": ("分享数", "分享"),
    "rise_fans": ("新增粉丝", "涨粉数", "涨粉"),
    "home_views": ("主页访问量", "主页访问"),
    "ctr": ("点击率", "进入率", "CTR"),
    "completion_rate": ("完播率", "阅读完成率"),
    "avg_view_time": ("平均观看时长", "平均阅读时长", "平均播放时长"),
    "top_source": ("流量来源", "主要来源"),
    "top_interest": ("兴趣标签", "主要兴趣"),
}


def _decode_json(text: str):
    raw = _ANSI_RE.sub("", str(text or "")).lstrip("\ufeff").strip()
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


def _unwrap(value):
    cur = value
    for _ in range(4):
        if not isinstance(cur, dict):
            break
        if "data" in cur and ("session" in cur or cur.get("ok") is True):
            cur = cur.get("data")
        elif set(cur).issubset({"data", "result"}) and (cur.get("data") is not None or cur.get("result") is not None):
            cur = cur.get("data", cur.get("result"))
        else:
            break
    return cur


def _run_browser(args: list[str], timeout: int = 90) -> tuple[object, dict | None]:
    if args and args[0] == "eval" and len(args) > 1:
        args = [args[0], str(args[1]).replace("\r", " ").replace("\n", " "), *args[2:]]
    cmd = [OPENCLI, "browser", SESSION, *[str(x) for x in args]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, {"code": "DATA_CENTER_TIMEOUT", "message": "创作者数据中心页面读取超时"}
    except OSError as exc:
        return None, {"code": "DATA_CENTER_BROWSER_UNAVAILABLE", "message": str(exc)}

    def decode(value: bytes | None) -> str:
        if not value:
            return ""
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("gb18030", errors="replace")

    out, err = decode(proc.stdout).strip(), decode(proc.stderr).strip()
    payload = _decode_json(out)
    if proc.returncode != 0:
        return None, {"code": "DATA_CENTER_BROWSER_ERROR", "message": (err or out or f"exit={proc.returncode}")[:500]}
    if payload is None:
        return out, None
    if isinstance(payload, dict) and (payload.get("ok") is False or payload.get("error")):
        raw_error = payload.get("error") or {}
        return None, {"code": str(raw_error.get("code") or "DATA_CENTER_BROWSER_ERROR"),
                      "message": str(raw_error.get("message") or raw_error)[:500]}
    return _unwrap(payload), None


def _metric_number(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    multiplier = 10000 if text.endswith(("万", "w", "W")) else 1000 if text.endswith(("k", "K")) else 1
    if multiplier != 1:
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _percent(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        number = float(text.rstrip("%"))
    except ValueError:
        return None
    return number / 100 if text.endswith("%") or number > 1 else number


def _duration(value):
    text = str(value or "").strip()
    if not text:
        return None
    minute = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min)", text, re.I)
    second = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s)", text, re.I)
    if minute or second:
        return (float(minute.group(1)) * 60 if minute else 0) + (float(second.group(1)) if second else 0)
    try:
        return float(text)
    except ValueError:
        return None


def _value_after_label(text: str, aliases: tuple[str, ...], suffix: str = _NUMBER_RE) -> tuple[str | None, str | None]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines() if line.strip()]
    for alias in aliases:
        pattern = re.compile(re.escape(alias) + r"\s*[:：]?\s*(" + suffix + r"%?(?:\s*(?:分钟|分|秒|min|s))?)", re.I)
        for idx, line in enumerate(lines):
            match = pattern.search(line)
            if match:
                return match.group(1), alias
            if alias.lower() == line.lower() or alias in line:
                for candidate in lines[idx + 1:idx + 4]:
                    nearby = re.fullmatch(r"\s*(" + suffix + r"%?(?:\s*(?:分钟|分|秒|min|s))?)\s*", candidate, re.I)
                    if nearby:
                        return nearby.group(1), alias
    return None, None


def normalize_capture(capture: dict) -> dict:
    """Convert visible official-page text into typed account metrics."""
    page_text = str(capture.get("page_text") or "") + "\n" + "\n".join(capture.get("svg_text") or [])
    metrics, evidence = {}, {}
    for key, aliases in ACCOUNT_METRICS.items():
        raw, label = _value_after_label(page_text, aliases)
        value = _metric_number(raw)
        if value is not None:
            metrics[key] = int(value) if float(value).is_integer() else value
            evidence[key] = {"label": label, "raw": raw}
    for key, aliases in RATIO_METRICS.items():
        raw, label = _value_after_label(page_text, aliases)
        value = _percent(raw)
        if value is not None:
            metrics[key] = value
            evidence[key] = {"label": label, "raw": raw}
    for key, aliases in DURATION_METRICS.items():
        raw, label = _value_after_label(page_text, aliases)
        value = _duration(raw)
        if value is not None:
            metrics[key] = value
            evidence[key] = {"label": label, "raw": raw}

    active_periods = []
    active_context = re.search(r"(?:粉丝|用户|受众).{0,20}活跃(?:时间|时段)(.{0,500})", page_text, re.S)
    if active_context:
        for start, end in re.findall(r"(\d{1,2}:\d{2})\s*[-–—至]\s*(\d{1,2}:\d{2})", active_context.group(1)):
            window = f"{start}-{end}"
            if window not in active_periods:
                active_periods.append(window)

    observed = {}
    lines = [re.sub(r"\s+", " ", line).strip() for line in page_text.splitlines() if line.strip()]
    for key, aliases in {
        "gender": ("性别分布", "粉丝性别"),
        "age": ("年龄分布", "粉丝年龄"),
        "region": ("地域分布", "地区分布"),
        "interest": ("兴趣分布", "兴趣标签"),
    }.items():
        for alias in aliases:
            match = re.search(re.escape(alias) + r"\s*[:：]?\s*([^\n]{2,120})", page_text)
            if match:
                observed[key] = match.group(1).strip()
                break
            for idx, line in enumerate(lines):
                if line == alias or alias in line:
                    block = [x for x in lines[idx + 1:idx + 9]
                             if x not in sum((list(v) for v in {
                                 "gender": ("性别分布", "粉丝性别"),
                                 "age": ("年龄分布", "粉丝年龄"),
                                 "region": ("地域分布", "地区分布"),
                                 "interest": ("兴趣分布", "兴趣标签"),
                             }.values()), [])]
                    if block:
                        observed[key] = " / ".join(block)[:500]
                    break
            if key in observed:
                break
    return {"account_metrics": metrics, "metric_evidence": evidence,
            "observed_audience": observed, "active_periods": active_periods}


def _header_index(headers: list[str], aliases: tuple[str, ...]) -> int | None:
    normalized = [re.sub(r"\s+", "", str(x or "")).lower() for x in headers]
    for idx, header in enumerate(normalized):
        if any(re.sub(r"\s+", "", alias).lower() in header for alias in aliases):
            return idx
    return None


def normalize_note_tables(capture: dict) -> list[dict]:
    """Extract per-note rows only when a visible table has title plus real metrics."""
    notes, seen = [], set()
    for table in capture.get("tables") or []:
        if not isinstance(table, dict):
            continue
        headers = [str(x or "") for x in (table.get("headers") or [])]
        rows = table.get("rows") or []
        if not headers and rows:
            headers = [str((x or {}).get("text") or "") for x in rows[0]]
            rows = rows[1:]
        indexes = {key: _header_index(headers, aliases) for key, aliases in NOTE_FIELDS.items()}
        title_idx = indexes.get("title")
        metric_keys = ("views", "impressions", "likes", "favorites", "comments", "shares",
                       "rise_fans", "ctr", "completion_rate", "avg_view_time")
        if title_idx is None or not any(indexes.get(key) is not None for key in metric_keys):
            continue
        for cells in rows:
            if not isinstance(cells, list) or title_idx >= len(cells):
                continue
            def cell(key: str) -> dict:
                idx = indexes.get(key)
                return cells[idx] if idx is not None and idx < len(cells) and isinstance(cells[idx], dict) else {}
            title = str(cell("title").get("text") or "").strip()
            if not title or title in seen:
                continue
            published = str(cell("published_at").get("text") or "").strip()
            note_id = str(cell("note_id").get("text") or "").strip()
            href = str(cell("title").get("href") or "")
            if not note_id and href:
                match = re.search(r"/(?:explore|discovery/item|note)/([A-Za-z0-9]+)", href)
                note_id = match.group(1) if match else ""
            if not note_id:
                note_id = "dc_" + hashlib.sha1((title + "|" + published).encode("utf-8")).hexdigest()[:16]
            note = {"note_id": note_id, "title": title, "published_at": published,
                    "source": "creator_data_center_dom", "source_url": capture.get("url", "")}
            for key in ("views", "impressions", "clicks", "likes", "favorites", "comments",
                        "shares", "rise_fans", "home_views"):
                raw = cell(key).get("text")
                value = _metric_number(raw)
                if value is not None:
                    note[key] = int(value) if float(value).is_integer() else value
            for key in ("ctr", "completion_rate"):
                value = _percent(cell(key).get("text"))
                if value is not None:
                    note[key] = value
            value = _duration(cell("avg_view_time").get("text"))
            if value is not None:
                note["avg_view_time"] = value
            for key in ("top_source", "top_interest"):
                value = str(cell(key).get("text") or "").strip()
                if value:
                    note[key] = value
            if any(key in note for key in metric_keys):
                seen.add(title)
                notes.append(note)
    return notes


def _page_kind(url: str, title: str, text: str) -> str:
    blob = (url + " " + title + " " + text[:1200]).lower()
    if any(key in blob for key in ("粉丝", "受众", "audience", "fans")):
        return "audience"
    if any(key in blob for key in ("笔记", "内容数据", "note", "content")):
        return "content"
    if any(key in blob for key in ("流量来源", "traffic", "source")):
        return "traffic"
    return "account"


def _section_name(kind: str, title: str, text: str, url: str = "") -> str:
    path = urlsplit(str(url or "")).path.rstrip("/")
    if path == "/statistics/fans-data":
        return "粉丝数据"
    if path == "/statistics/data-analysis":
        return "内容分析"
    if path == "/statistics/account/v2":
        return "账号概览"
    blob = title + " " + text[:500]
    if "粉丝数据" in blob or kind == "audience":
        return "粉丝数据"
    if "内容分析" in blob or kind == "content":
        return "内容分析"
    return "账号概览"


def _nav_section(label: str) -> str:
    text = str(label or "")
    if "粉丝" in text or "受众" in text:
        return "粉丝数据"
    if "内容" in text or "数据分析" in text or "笔记" in text:
        return "内容分析"
    return "账号概览"


def _safe_capture(capture: dict) -> dict:
    """Persist only visible analytics text; never persist cookies or tokens."""
    return {
        "url": str(capture.get("url") or "")[:500],
        "title": str(capture.get("title") or "")[:200],
        "page_text": str(capture.get("page_text") or "")[:80000],
        "svg_text": [str(x)[:200] for x in (capture.get("svg_text") or [])[:500]],
        "links": [{"url": str(x.get("url") or "")[:500], "text": str(x.get("text") or "")[:120]}
                  for x in (capture.get("links") or [])[:100] if isinstance(x, dict)],
        "nav_items": [str(x)[:80] for x in (capture.get("nav_items") or [])[:50] if str(x).strip()],
        "tables": (capture.get("tables") or [])[:30],
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "source": "creator_data_center_dom",
    }


def _canonical_statistics_url(value: str) -> str | None:
    try:
        parts = urlsplit(str(value or ""))
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc != "creator.xiaohongshu.com":
        return None
    if not parts.path.startswith("/statistics/"):
        return None
    # Date/filter queries create duplicates of the same analytics page; filters are
    # read from the visible default page and the canonical route is crawled once.
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", "", ""))


def _capture_current_page() -> tuple[dict | None, dict | None]:
    _run_browser(["wait", "selector", "body", "--timeout", "20000"], timeout=30)
    time.sleep(1.0)
    script = r"""(() => {
      const clean=v=>String(v||'').replace(/\u200b/g,'').replace(/[ \t]+/g,' ').trim();
      const text=clean(document.body&&(document.body.innerText||document.body.textContent)||'');
      const lower=text.toLowerCase();
      const login=/\u626b\u7801\u767b\u5f55|\u624b\u673a\u53f7\u767b\u5f55|\u767b\u5f55\u540e/.test(text)||/\/login(?:\?|$)/.test(location.href);
      const security=/\u5b89\u5168\u9a8c\u8bc1|\u5b89\u5168\u9650\u5236|\u8bf7\u6c42\u9891\u7e41|captcha|security verification/.test(lower);
      const svg=[...document.querySelectorAll('svg text')].map(n=>clean(n.textContent)).filter(Boolean).slice(0,500);
      const links=[]; const seen=new Set();
      for(const a of document.querySelectorAll('a[href]')){try{const u=new URL(a.href,location.href);if(u.origin!==location.origin||!u.pathname.startsWith('/statistics/'))continue;u.hash='';const k=u.origin+u.pathname;if(seen.has(k))continue;seen.add(k);links.push({url:k,text:clean(a.innerText||a.textContent)})}catch(e){}}
      const navItems=[];const navSeen=new Set();const navRe=/\u6570\u636e|\u5206\u6790|\u7c89\u4e1d|\u53d7\u4f17|\u5185\u5bb9|\u7b14\u8bb0|\u8d26\u53f7|\u6d41\u91cf/;
      for(const n of document.querySelectorAll('nav li,aside li,[role="menuitem"],[class*="menu"] li,[class*="menu"] [class*="item"],[class*="sidebar"] li,[class*="sidebar"] [class*="item"]')){const t=clean(n.innerText||n.textContent);if(t.length<2||t.length>30||!navRe.test(t)||navSeen.has(t))continue;navSeen.add(t);navItems.push(t)}
      const tables=[];
      for(const table of [...document.querySelectorAll('table,[role="table"]')].slice(0,30)){
        const headers=[...table.querySelectorAll('thead th,[role="columnheader"]')].map(n=>clean(n.innerText||n.textContent));
        const rowNodes=[...table.querySelectorAll('tbody tr,[role="row"]')];
        const rows=rowNodes.slice(0,200).map(row=>[...row.querySelectorAll('td,th,[role="cell"],[role="gridcell"]')].map(cell=>{const a=cell.querySelector('a[href]');return{text:clean(cell.innerText||cell.textContent),href:a?a.href:''}})).filter(r=>r.length);
        if(headers.length||rows.length)tables.push({headers,rows});
      }
      return {url:location.href,title:document.title,page_text:text.slice(0,80000),svg_text:svg,links,nav_items:navItems.slice(0,50),tables,login_required:login,security_check:security};
    })()"""
    capture, err = _run_browser(["eval", script], timeout=40)
    if err or not isinstance(capture, dict):
        return None, err or {"code": "DATA_CENTER_EMPTY", "message": "数据中心DOM没有返回结构化结果"}
    return capture, None


def collect_account_data() -> dict:
    """Discover and read the whole visible Creator Center statistics system."""
    blocked = operation_policy.blocked_result("creator_center_collect")
    if blocked:
        return blocked
    max_pages = max(1, min(20, int(os.environ.get("XHS_DATA_CENTER_MAX_PAGES", "12"))))
    queue, seen, pages = list(SECTION_URLS.values()), set(), []
    nav_queue, seen_nav, page_signatures = [], set(), set()
    stop_warning = ""
    while (queue or nav_queue) and len(pages) < max_pages:
        url = None
        if queue:
            url = _canonical_statistics_url(queue.pop(0))
            if not url or url in seen:
                continue
            seen.add(url)
            _, err = _run_browser(["open", url, "--window", "foreground"], timeout=90)
        else:
            label = nav_queue.pop(0)
            if label in seen_nav:
                continue
            seen_nav.add(label)
            if _nav_section(label) in {p.get("section") for p in pages}:
                continue
            label_js = json.dumps(label, ensure_ascii=True)
            click_js = ("(()=>{const q=" + label_js + ";const clean=v=>String(v||'').replace(/\\s+/g,' ').trim();"
                        "const nodes=[...document.querySelectorAll('nav li,aside li,[role=\"menuitem\"],[class*=\"menu\"] li,[class*=\"menu\"] [class*=\"item\"],[class*=\"sidebar\"] li,[class*=\"sidebar\"] [class*=\"item\"]')];"
                        "const n=nodes.find(x=>x.offsetParent!==null&&clean(x.innerText||x.textContent)===q);"
                        "if(!n)return{ok:false};n.click();return{ok:true,text:q}})()")
            clicked, err = _run_browser(["eval", click_js], timeout=20)
            if not err and (not isinstance(clicked, dict) or not clicked.get("ok")):
                err = {"code": "DATA_CENTER_NAV_NOT_FOUND", "message": f"侧边栏分区不可点击: {label}"}
        if err:
            if url is None and pages:
                stop_warning = str(err.get("message") or err)[:300]
                continue
            if not pages:
                return {"ok": False, "source": "creator_data_center_dom", "url": ACCOUNT_URL, "error": err}
            stop_warning = str(err.get("message") or err)[:300]
            break
        capture, err = _capture_current_page()
        if err or not capture:
            if not pages:
                return {"ok": False, "source": "creator_data_center_dom", "url": ACCOUNT_URL, "error": err}
            stop_warning = str((err or {}).get("message") or err or "页面读取失败")[:300]
            break
        if capture.get("login_required"):
            return {"ok": False, "source": "creator_data_center_dom", "url": ACCOUNT_URL,
                    "manual_verification_required": True,
                    "error": {"code": "AUTH", "message": "请在保留的xhs-ui窗口登录小红书创作服务平台"}}
        if capture.get("security_check"):
            return {"ok": False, "source": "creator_data_center_dom", "url": ACCOUNT_URL,
                    "manual_verification_required": True,
                    "error": {"code": "SECURITY_CHECK", "message": "数据中心要求人工安全验证，窗口已保留"}}
        safe = _safe_capture(capture)
        signature = hashlib.sha1((str(safe.get("url")) + "|" + str(safe.get("page_text"))[:3000]).encode("utf-8")).hexdigest()
        if signature in page_signatures:
            continue
        page_signatures.add(signature)
        normalized = normalize_capture(safe)
        notes = normalize_note_tables(safe)
        kind = _page_kind(safe.get("url", ""), safe.get("title", ""), safe.get("page_text", ""))
        section = _section_name(kind, safe.get("title", ""), safe.get("page_text", ""), safe.get("url", ""))
        pages.append({**safe, **normalized, "notes": notes, "kind": kind, "section": section})
        for link in safe.get("links") or []:
            candidate = _canonical_statistics_url(link.get("url", ""))
            if candidate and candidate not in seen and candidate not in queue:
                queue.append(candidate)
        for label in safe.get("nav_items") or []:
            if label not in seen_nav and label not in nav_queue:
                nav_queue.append(label)

    metrics, evidence, observed, periods, notes_by_key = {}, {}, {}, [], {}
    for page in pages:
        for key, value in (page.get("account_metrics") or {}).items():
            if key not in metrics:
                metrics[key] = value
                ev = dict((page.get("metric_evidence") or {}).get(key, {}))
                ev.update({"page_url": page.get("url"), "page_kind": page.get("kind")})
                evidence[key] = ev
        observed.update(page.get("observed_audience") or {})
        for period in page.get("active_periods") or []:
            if period not in periods:
                periods.append(period)
        for note in page.get("notes") or []:
            key = str(note.get("note_id") or note.get("title") or "")
            if key:
                notes_by_key[key] = {**notes_by_key.get(key, {}), **note}

    RAW.mkdir(parents=True, exist_ok=True)
    raw_path = RAW / ("creator_data_center_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    raw_path.write_text(json.dumps({"source": "creator_data_center_dom", "entry_url": ACCOUNT_URL,
                                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                                    "pages": pages, "account_metrics": metrics,
                                    "metric_evidence": evidence, "observed_audience": observed,
                                    "active_periods": periods, "notes": list(notes_by_key.values())},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    summaries = [{"url": p.get("url"), "title": p.get("title"), "kind": p.get("kind"),
                  "section": p.get("section"),
                  "metrics": list((p.get("account_metrics") or {}).keys()),
                  "notes": len(p.get("notes") or [])} for p in pages]
    found_sections = list(dict.fromkeys(p.get("section") for p in pages if p.get("section")))
    missing_sections = [x for x in REQUIRED_SECTIONS if x not in found_sections]
    return {"ok": True, "source": "creator_data_center_dom", "url": ACCOUNT_URL,
            "raw_path": str(raw_path.relative_to(ROOT)), "account_metrics": metrics,
            "metric_evidence": evidence, "observed_audience": observed,
            "active_periods": periods, "notes": list(notes_by_key.values()),
            "pages": summaries, "page_count": len(pages),
            "found_sections": found_sections, "missing_sections": missing_sections,
            "warning": stop_warning or (("未读取栏目: " + " / ".join(missing_sections)) if missing_sections else "")
                       or ("页面图表未在DOM暴露的字段保持缺失" if not metrics else "")}
