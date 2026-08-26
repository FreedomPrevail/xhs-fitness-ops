#!/usr/bin/env python3
"""健身样本采集 —— 批量搜小红书健身热帖,建样本库。

基础搜索只拿 title/author/likes/url/published_at；搜索结束后仅对当天
全局点赞最高的少量唯一帖子做详情读取，补收藏和评论，避免把请求量放大。
"""
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("_collect_mod", ROOT / "collect" / "collect.py")
collect = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(collect)

DB = ROOT / "data" / "sample.db"

KEYWORDS = [
    "居家健身", "减脂餐", "新手健身", "运动损伤", "健身减肥",
    "增肌", "帕梅拉", "健身房新手", "拉伸放松", "马甲线",
    # 耐力/有氧
    "马拉松训练", "跑步", "骑行", "游泳", "跳绳", "越野跑",
    # 力量/增肌
    "力量训练", "撸铁", "器械健身", "功能性训练",
    # 球类/户外
    "羽毛球", "篮球", "足球", "登山", "徒步", "户外运动",
    # 柔韧/康复
    "瑜伽", "普拉提", "体态矫正", "运动康复",
]

# 多运动子领域保底采样词:确保采集不只在减脂/塑形赛道收敛。
# 每轮至少从每个领域抽一个词,与实时热搜混合,覆盖更广的健身场景。
DOMAIN_SEEDS = {
    "减脂塑形": ["减脂", "瘦身", "燃脂", "塑形", "居家健身"],
    "耐力有氧": ["马拉松训练", "跑步", "骑行", "游泳", "跳绳", "越野跑"],
    "力量增肌": ["力量训练", "撸铁", "增肌", "器械健身", "功能性训练"],
    "球类户外": ["羽毛球", "篮球", "足球", "登山", "徒步", "户外运动"],
    "柔韧康复": ["瑜伽", "普拉提", "体态矫正", "运动康复", "拉伸"],
}


def diverse_keywords(trending, slots=6):
    """实时热搜与各运动子领域保底词混合,保证采集广度,避免全收敛在减脂赛道。

    热搜(实时热点)只占少量位置,其余按领域逐日轮转补词,让马拉松/力量/球类/
    户外/康复等场景每轮都有采样。
    """
    out = []
    seen = set()
    trending_cap = max(1, int(slots) - len(DOMAIN_SEEDS))
    for kw in (trending or []):
        kw = str(kw).strip()
        if kw and kw not in seen:
            out.append(kw)
            seen.add(kw)
            if len(out) >= trending_cap:
                break
    day = date.today().toordinal()
    for _domain, seeds in DOMAIN_SEEDS.items():
        if len(out) >= int(slots):
            break
        pick = seeds[day % len(seeds)]
        if pick not in seen:
            out.append(pick)
            seen.add(pick)
    return out[: int(slots)]


DOM_SEARCH_SESSION = "xhs-fitness-search"


def _unwrap_browser_payload(payload):
    """兼容 browser 命令的 raw 值与 {session,data} 包装。"""
    value = payload
    for _ in range(3):
        if not isinstance(value, dict):
            break
        if value.get("ok") is False:
            break
        if "data" in value and ("session" in value or set(value).issubset({"data", "session", "page", "tab"})):
            value = value.get("data")
            continue
        break
    return value


def _run_browser(args: list[str], timeout: int = 90) -> tuple[object, dict | None]:
    """执行稳定命名的 opencli browser 会话；不依赖损坏的 XHS search adapter。"""
    # opencli 1.8.6 的 `browser eval` 会把多行 JS 拆坏（Windows 命令行参数换行
    # 被截断），表现为 "SyntaxError: Unexpected token ')'"。eval 前把换行压成空格。
    if args and args[0] == "eval" and len(args) > 1 and isinstance(args[1], str):
        args = [args[0], args[1].replace("\r\n", " ").replace("\n", " ").replace("\r", " "), *args[2:]]
    cmd = [collect.OPENCLI, "browser", DOM_SEARCH_SESSION, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return None, {"code": "DOM_TIMEOUT", "message": f"Browser DOM 命令超时: {' '.join(cmd[:5])}"}
    except OSError as exc:
        return None, {"code": "DOM_BROWSER_UNAVAILABLE", "message": str(exc)}

    out = (proc.stdout or "").strip()
    err_text = (proc.stderr or "").strip()
    if not out:
        return None, {
            "code": "DOM_BROWSER_ERROR",
            "message": (err_text or f"Browser DOM 命令无输出，exit={proc.returncode}")[:500],
        }
    try:
        payload = collect._decode_json_output(out)
    except ValueError:
        # browser wait/keys/close 成功时输出简短文本，不是 JSON。
        if proc.returncode == 0:
            return out, None
        return None, {
            "code": "DOM_BROWSER_NON_JSON",
            "message": (out[:300] + (" | " + err_text[:160] if err_text else "")),
        }
    if isinstance(payload, dict) and (payload.get("ok") is False or payload.get("error")):
        error = payload.get("error") or {}
        return None, {
            "code": str(error.get("code") or "DOM_BROWSER_ERROR").upper(),
            "message": str(error.get("message") or err_text or payload)[:500],
        }
    if proc.returncode != 0:
        return None, {
            "code": "DOM_BROWSER_ERROR",
            "message": str(err_text or payload)[:500],
        }
    return _unwrap_browser_payload(payload), None


def _close_dom_session():
    """释放项目拥有的 browser session；关闭失败不覆盖真正的采集结果。"""
    try:
        _run_browser(["close"], timeout=20)
    except Exception:
        pass


def open_manual_verification_window(reason: str = "") -> dict:
    """打开并保留公共站前台窗口，供用户人工处理登录/安全认证。"""
    _, err = _run_browser([
        "open", "https://www.xiaohongshu.com/explore", "--window", "foreground"
    ], timeout=90)
    if err:
        return {
            "ok": False,
            "manual_verification_required": True,
            "session": DOM_SEARCH_SESSION,
            "error": str(err.get("message") or err)[:300],
        }
    return {
        "ok": True,
        "manual_verification_required": True,
        "session": DOM_SEARCH_SESSION,
        "message": reason or "前台小红书窗口已保留，请人工完成登录/安全认证",
    }


def search_via_browser_dom(keyword: str, limit: int = 20) -> tuple[list[dict] | None, dict | None]:
    """从 Explore 正常 UI 进入搜索，再读取页面卡片。

    这条路径只在官方 `xiaohongshu search` 返回 EMPTY_RESULT 后启用。
    输入/提交使用 browser fill/keys；eval 严格只读 DOM。
    """
    explore = "https://www.xiaohongshu.com/explore"
    # V6 使用前台持久 session。若页面弹出登录/安全二维码，用户必须有足够
    # 时间人工扫码；任务返回后该 session 也不会在认证状态下被关闭。
    _, err = _run_browser(["open", explore, "--window", "foreground"], timeout=90)
    if err:
        return None, err

    # 正常 UI 流程比直接拼 search_result URL 更不容易落到安全验证页。
    submitted = False
    input_selector = ""
    for selector in ("#search-input", "input[placeholder*='搜索']", "input[type='search']"):
        _, wait_err = _run_browser(["wait", "selector", selector, "--timeout", "5000"], timeout=12)
        if not wait_err:
            input_selector = selector
            break
    if not input_selector:
        # 仍执行一次只读诊断，让下方返回明确的登录墙/安全验证，而不是 selector_not_found。
        pass
    else:
        _, err = _run_browser(["fill", input_selector, keyword], timeout=30)
        if err:
            return None, err
        _, err = _run_browser(["keys", "Enter"], timeout=30)
        if err:
            return None, err
        submitted = True
        _run_browser([
            "wait", "selector",
            "a[href*='/search_result/'],a[href*='/explore/'],section.note-item",
            "--timeout", "15000",
        ], timeout=25)

    # 不依赖 section.note-item；从真实笔记链接向上找卡片，兼容 class 被移除/改名。
    js = r"""(() => {
      const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
      const pageText = clean(document.body && document.body.innerText);
      const lower = pageText.toLowerCase();
      const loginWall = /登录后查看搜索结果|登录后查看|扫码登录|手机号登录/.test(pageText);
      const security = /安全验证|安全限制|访问链接异常|请求频繁|security verification|captcha/.test(lower)
        || /website-login\/(?:captcha|error)|\/captcha/.test(location.href);
      const qrVisible = Array.from(document.querySelectorAll("img,canvas,[class*='qrcode'],[class*='qr-code'],[class*='qrCode']"))
        .some((n) => {
          const r = n.getBoundingClientRect();
          const src = String(n.getAttribute && n.getAttribute('src') || '').toLowerCase();
          const cls = String(n.className || '').toLowerCase();
          return r.width >= 80 && r.height >= 80 && (src.includes('qr') || cls.includes('qr') || security || loginWall);
        });
      const anchors = Array.from(document.querySelectorAll('a[href]')).filter((a) => {
        const h = a.getAttribute('href') || '';
        return /\/(search_result|explore)\/[0-9a-zA-Z]+/.test(h);
      }).sort((a, b) => Number((b.href || '').includes('xsec_token=')) - Number((a.href || '').includes('xsec_token=')));
      const seen = new Set();
      const items = [];
      const countRx = /^\d+(?:\.\d+)?(?:万|[kKwW])?$/;
      const pickCard = (a) => {
        let n = a;
        for (let i = 0; i < 7 && n; i += 1, n = n.parentElement) {
          const txt = clean(n.innerText);
          if (txt.length >= 4 && txt.length <= 500 && n.querySelector && n.querySelector("a[href*='/user/profile/']")) return n;
        }
        return a.closest('section,article,li') || a.parentElement || a;
      };
      for (const a of anchors) {
        if (items.length >= %LIMIT%) break;
        let url = '';
        try { url = new URL(a.getAttribute('href'), location.href).href; } catch (_) { continue; }
        const idMatch = url.match(/\/(?:search_result|explore)\/([0-9a-zA-Z]+)/);
        const noteId = idMatch ? idMatch[1] : '';
        if (!noteId || seen.has(noteId)) continue;
        const card = pickCard(a);
        const authorA = card.querySelector && card.querySelector("a[href*='/user/profile/']");
        const explicitTitle = card.querySelector && card.querySelector(".title,.note-title,[class*='title'],[data-testid*='title']");
        const lines = String(card.innerText || '').split(/\n+/).map(clean).filter(Boolean);
        const author = clean(authorA && (authorA.innerText || authorA.getAttribute('title')));
        let title = clean((explicitTitle && (explicitTitle.innerText || explicitTitle.getAttribute('title'))) || a.getAttribute('title') || a.innerText);
        if (!title || title === author || countRx.test(title)) {
          title = lines.find((x) => x !== author && !countRx.test(x) && x.length >= 2 && x.length <= 100) || '';
        }
        const likeNode = card.querySelector && card.querySelector("[class*='like'] [class*='count'],[class*='like-count'],[class*='count']");
        let likes = clean(likeNode && likeNode.innerText);
        if (!countRx.test(likes)) likes = lines.slice().reverse().find((x) => countRx.test(x)) || '';
        const timeNode = card.querySelector && card.querySelector("time,[class*='time'],[class*='date']");
        const videoMark = card.querySelector && card.querySelector("video,[class*='video-icon'],[class*='play-icon'],[class*='playIcon'],svg[class*='play']");
        const imageTextMark = card.querySelector && card.querySelector("[class*='carousel'],[class*='swiper-pagination'],[class*='multi-image'],[class*='image-count']");
        seen.add(noteId);
        items.push({
          rank: items.length + 1,
          title,
          author,
          likes,
          published_at: clean(timeNode && (timeNode.innerText || timeNode.getAttribute('datetime'))),
          url,
          note_id: noteId,
          content_type: videoMark ? 'video' : (imageTextMark ? 'image_text' : 'unknown'),
          content_type_source: videoMark ? 'dom:video_marker' : (imageTextMark ? 'dom:image_carousel' : 'unknown'),
          source: 'browser_dom_fallback'
        });
      }
      return {
        items,
        login_wall: loginWall,
        security,
        qr_visible: qrVisible,
        page_title: document.title,
        page_url: location.href,
        anchor_count: anchors.length,
        text_preview: pageText.slice(0, 180)
      };
    })()""".replace("%LIMIT%", str(max(1, min(int(limit), 30))))
    payload, err = _run_browser(["eval", js], timeout=45)
    if err:
        return None, err
    if not isinstance(payload, dict):
        return None, {"code": "DOM_BAD_PAYLOAD", "message": f"DOM 返回结构异常: {str(payload)[:240]}"}
    if payload.get("security"):
        return None, {
            "code": "MANUAL_VERIFICATION_REQUIRED",
            "message": f"搜索页触发安全认证；前台窗口已保留，请扫码后在看板点击“已扫码，检查认证”: {payload.get('page_title')}",
            "manual_verification_required": True,
            "session": DOM_SEARCH_SESSION,
            "page_url": str(payload.get("page_url") or ""),
        }
    if payload.get("login_wall") or payload.get("qr_visible"):
        return None, {
            "code": "MANUAL_VERIFICATION_REQUIRED",
            "message": "公共站需要登录/扫码；前台窗口已保留，请完成后在看板点击“已扫码，检查认证”",
            "manual_verification_required": True,
            "session": DOM_SEARCH_SESSION,
            "page_url": str(payload.get("page_url") or ""),
        }
    if not submitted:
        return None, {
            "code": "DOM_SEARCH_INPUT_NOT_FOUND",
            "message": (
                f"未找到 #search-input，未执行搜索；title={payload.get('page_title')}, "
                f"url={payload.get('page_url')}, text={payload.get('text_preview')}"
            )[:500],
        }
    items = payload.get("items") or []
    if not items:
        return None, {
            "code": "DOM_EMPTY",
            "message": (
                f"DOM 兜底仍未识别笔记卡片；title={payload.get('page_title')}, "
                f"url={payload.get('page_url')}, anchors={payload.get('anchor_count')}, "
                f"text={payload.get('text_preview')}"
            )[:500],
        }
    return items, None


def verification_status() -> dict:
    """检查V6保留的前台认证会话；只读，不点击、不提交、不绕过验证。"""
    js = r"""(() => {
      const text = String(document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
      const lower = text.toLowerCase();
      const login = /登录后查看搜索结果|扫码登录|手机号登录/.test(text);
      const security = /安全验证|安全限制|访问链接异常|请求频繁|security verification|captcha/.test(lower)
        || /website-login\/(?:captcha|error)|\/captcha/.test(location.href);
      const cards = document.querySelectorAll("a[href*='/search_result/'],a[href*='/explore/'],section.note-item").length;
      const qr = Array.from(document.querySelectorAll("img,canvas,[class*='qrcode'],[class*='qr-code'],[class*='qrCode']"))
        .some((n) => {
          const r = n.getBoundingClientRect();
          const src = String(n.getAttribute && n.getAttribute('src') || '').toLowerCase();
          const cls = String(n.className || '').toLowerCase();
          return r.width >= 80 && r.height >= 80 && (src.includes('qr') || cls.includes('qr') || security || login);
        });
      return {url: location.href, title: document.title, login, security, qr, cards, preview: text.slice(0, 160)};
    })()"""
    payload, err = _run_browser(["eval", js, "--window", "foreground"], timeout=45)
    if err:
        return {"ok": False, "verified": False, "error": str(err.get("message") or err), "code": err.get("code", "")}
    if not isinstance(payload, dict):
        return {"ok": False, "verified": False, "error": f"认证状态返回异常: {str(payload)[:200]}"}
    waiting = bool(payload.get("login") or payload.get("security") or payload.get("qr"))
    verified = not waiting and int(payload.get("cards") or 0) > 0
    return {
        "ok": True,
        "verified": verified,
        "waiting": waiting,
        "cards": int(payload.get("cards") or 0),
        "url": str(payload.get("url") or ""),
        "title": str(payload.get("title") or ""),
        "message": (
            "认证已通过，可以重新点击“一键选题科学化”"
            if verified else
            "仍在登录/安全认证页面，请在保留的前台窗口完成扫码"
            if waiting else
            "认证页已离开，但还未看到搜索结果；请在该窗口手动搜索一次“新手健身”"
        ),
    }


def parse_count(s) -> int:
    """把 1.1万 / 2.3k / 2105 等计数统一转整数。"""
    s = str(s or "").strip().lower().replace(",", "")
    m = re.match(r"([\d.]+)\s*万", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"([\d.]+)\s*k", s, re.I)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.match(r"([\d.]+)\s*w", s, re.I)
    if m:
        return int(float(m.group(1)) * 10000)
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else 0


def parse_likes(s: str) -> int:
    """向后兼容旧代码。"""
    return parse_count(s)


def normalize_content_type(value) -> str:
    text=str(value or "").strip().lower().replace("-","_")
    if text in {"video","视频","短视频","movie"} or "video" in text or "视频" in text:
        return "video"
    if text in {"image_text","image","images","note","normal","图文","图片","图文笔记"}:
        return "image_text"
    if "image" in text or "图文" in text:
        return "image_text"
    return "unknown"


def detect_content_type(payload) -> tuple[str,str]:
    """按结构化字段识别媒介；不根据标题/正文词义猜测。"""
    queue=[payload]; seen=0
    while queue and seen<50:
        cur=queue.pop(0); seen+=1
        if isinstance(cur,str):
            raw=cur.strip()
            if raw.startswith(("{","[")):
                try: queue.append(json.loads(raw))
                except Exception: pass
            continue
        if isinstance(cur,list):
            queue.extend(cur[:12]); continue
        if not isinstance(cur,dict): continue
        for key in ("content_type","media_type","mediaType","note_type","noteType","type"):
            detected=normalize_content_type(cur.get(key))
            if detected!="unknown":
                source=str(cur.get("content_type_source") or f"field:{key}")
                return detected,source
        for key in ("video_url","videoUrl","video_info","videoInfo","video_resource","videoResource"):
            if cur.get(key) not in (None,"",[],{}): return "video",f"field:{key}"
        for key in ("image_list","imageList","images","image_info_list","imageInfoList"):
            if isinstance(cur.get(key),list) and cur[key]: return "image_text",f"field:{key}"
        for key in ("data","note","item","result","note_card","noteCard","raw","detail","detail_json"):
            if cur.get(key) not in (None,""): queue.append(cur[key])
    return "unknown","unknown"


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            collected TEXT, keyword TEXT, rank INTEGER,
            title TEXT, author TEXT, likes_raw TEXT, likes_num INTEGER,
            published_at TEXT, url TEXT,
            PRIMARY KEY (collected, keyword, url)
        )""")

    # 兼容已有 sample.db：只补列，不重建/清空旧数据。
    cols = {r[1] for r in con.execute("PRAGMA table_info(samples)").fetchall()}
    migrations = [
        ("collects_raw", "TEXT DEFAULT ''"),
        ("collects_num", "INTEGER DEFAULT 0"),
        ("comments_raw", "TEXT DEFAULT ''"),
        ("comments_num", "INTEGER DEFAULT 0"),
        ("detail_fetched", "INTEGER DEFAULT 0"),
        ("detail_collected", "TEXT DEFAULT ''"),
        ("collected_at", "TEXT DEFAULT ''"),
        ("note_id", "TEXT DEFAULT ''"),
        ("body", "TEXT DEFAULT ''"),
        ("cover_url", "TEXT DEFAULT ''"),
        ("detail_json", "TEXT DEFAULT ''"),
        ("content_type", "TEXT DEFAULT 'unknown'"),
        ("content_type_source", "TEXT DEFAULT 'unknown'"),
    ]
    for name, ddl in migrations:
        if name not in cols:
            con.execute(f"ALTER TABLE samples ADD COLUMN {name} {ddl}")
    con.execute("UPDATE samples SET content_type='unknown' WHERE COALESCE(content_type,'')='' ")
    con.execute("UPDATE samples SET content_type_source='unknown' WHERE COALESCE(content_type_source,'')='' ")
    # 对已有详情JSON只做结构化回填；仍然不从标题/正文猜媒介。
    legacy=con.execute("""SELECT rowid,detail_json FROM samples
                          WHERE content_type='unknown' AND COALESCE(detail_json,'')!=''""").fetchall()
    for rowid,detail_json in legacy:
        detected,source=detect_content_type(detail_json)
        if detected!="unknown":
            con.execute("UPDATE samples SET content_type=?,content_type_source=? WHERE rowid=?",
                        (detected,"migration:"+source,rowid))
    con.commit()
    return con


def _upsert_search_result(con, today: str, keyword: str, r: dict):
    """写基础搜索结果；重复采集时保留已补到的详情字段。"""
    likes_raw = str(r.get("likes") or r.get("like_count") or "")
    content_type,content_type_source=detect_content_type(r)
    con.execute(
        """
        INSERT INTO samples (
            collected, keyword, rank, title, author, likes_raw, likes_num,
            published_at, url, collected_at, note_id, content_type, content_type_source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(collected, keyword, url) DO UPDATE SET
            rank=excluded.rank,
            title=excluded.title,
            author=excluded.author,
            likes_raw=excluded.likes_raw,
            likes_num=excluded.likes_num,
            published_at=excluded.published_at,
            collected_at=excluded.collected_at,
            note_id=CASE WHEN excluded.note_id!='' THEN excluded.note_id ELSE samples.note_id END,
            content_type=CASE
              WHEN samples.content_type_source LIKE 'detail:%' OR samples.content_type_source='manual' THEN samples.content_type
              WHEN excluded.content_type!='unknown' THEN excluded.content_type ELSE samples.content_type END,
            content_type_source=CASE
              WHEN samples.content_type_source LIKE 'detail:%' OR samples.content_type_source='manual' THEN samples.content_type_source
              WHEN excluded.content_type!='unknown' THEN excluded.content_type_source ELSE samples.content_type_source END
        """,
        (
            today, keyword, r.get("rank", 0), r.get("title", ""),
            r.get("author") or r.get("user") or "", likes_raw, parse_count(likes_raw),
            r.get("published_at") or r.get("time") or "", r.get("url") or "",
            datetime.now().isoformat(timespec="seconds"),
            str(r.get("note_id") or r.get("noteId") or r.get("id") or ""),
            content_type,content_type_source,
        ),
    )


def _unwrap_detail(payload):
    if not isinstance(payload, dict):
        return payload if isinstance(payload, list) else {}
    for key in ("data", "note", "item", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _pick(d: dict, keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _keyword_cache_fresh(con, keyword: str, cache_hours: int = 8) -> tuple[bool, int]:
    """同一关键词近期已采过则复用数据库，减少重复平台请求。"""
    row = con.execute(
        "SELECT MAX(collected_at), COUNT(*) FROM samples WHERE keyword=? AND collected_at!=''",
        (keyword,),
    ).fetchone()
    if not row or not row[0]:
        return False, 0
    try:
        ts = datetime.fromisoformat(row[0])
    except ValueError:
        return False, int(row[1] or 0)
    return datetime.now() - ts < timedelta(hours=cache_hours), int(row[1] or 0)


def _must_stop_batch(err: dict | None) -> bool:
    """仅在继续请求会加剧风险或必然失败时停止整批。

    单条数据格式、URL 或 CLI 执行错误只跳过当前笔记；不能因此阻断后续候选。
    """
    if not err:
        return False
    code = str(err.get("code", "")).upper()
    msg = str(err.get("message", "")).lower()
    return (code in {"RATE_LIMITED", "SECURITY_CHECK", "SECURITY_BLOCK", "AUTH", "MANUAL_VERIFICATION_REQUIRED",
                     "CAPTCHA_REQUIRED"}
            or "429" in msg or "验证码" in msg or "安全验证" in msg or "安全限制" in msg
            or "风控" in msg or "security block" in msg or "security check" in msg
            or "security verification" in msg or "rate limit" in msg or "too many requests" in msg
            or "请先登录" in msg or "login required" in msg or "not logged in" in msg)


def enrich_top_samples(collected: str | None = None, top_n: int = 5,
                       candidate_urls: list[str] | set[str] | None = None) -> dict:
    """只对当天全局 Top-N 唯一帖子读取详情，补收藏和评论。

    `note` 需要 search 返回的完整 signed URL（通常带 xsec_token）。
    同一天已经补过详情的 URL 不再重复读取。
    """
    collected = collected or date.today().isoformat()
    con = init_db()
    params: list[object] = [collected]
    candidate_clause = ""
    if candidate_urls is not None:
        clean_urls = sorted({str(u) for u in candidate_urls if u and "xsec_token=" in str(u)})
        if not clean_urls:
            con.close()
            return {"enriched":0,"attempted":0,"selected":0,"failed":[],"hard_stop":None,
                    "skipped":"本轮没有新鲜且带xsec_token的详情候选，不读取缓存详情"}
        candidate_clause = " AND url IN (" + ",".join("?" for _ in clean_urls) + ")"
        params.extend(clean_urls)
    params.append(int(top_n))
    rows = con.execute(
        f"""
        SELECT url, MAX(likes_num) AS likes_num, MAX(title) AS title
        FROM samples
        WHERE collected=? AND url LIKE '%xsec_token=%'
          {candidate_clause}
        GROUP BY url
        HAVING MAX(COALESCE(detail_fetched,0))=0
        ORDER BY likes_num DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    enriched = 0
    attempted = 0
    failed = []
    hard_stop = None
    for i, (url, _, title) in enumerate(rows):
        if i > 0:
            # 少量顺序读取，避免一口气把详情请求堆叠起来。
            time.sleep(2.5 + random.uniform(0, 1.5))
        attempted += 1
        try:
            detail, err = collect.run_cli(["note", url], retries=0)
        except RuntimeError as e:
            detail, err = None, {"code": "RUNTIME", "message": str(e)}

        if err:
            item={"url":url,"title":title or "","code":err.get("code", ""),
                  "message":str(err.get("message", ""))[:240],"help":str(err.get("help", ""))[:180]}
            failed.append(item)
            # 登录/限流/安全限制时不继续追加详情请求。
            if _must_stop_batch(err):
                hard_stop=item
                break
            continue

        content_type,content_type_source=detect_content_type(detail)
        d = _unwrap_detail(detail)
        if not isinstance(d, dict):
            failed.append({"url":url,"title":title or "","code":"BAD_DETAIL","message":"note 返回结构无法解析"})
            continue

        likes_raw = str(_pick(d, ["likes", "like_count", "likeCount", "点赞", "点赞数"], ""))
        collects_raw = str(_pick(d, ["collects", "favorites", "favorite_count", "collect_count", "收藏", "收藏数"], ""))
        comments_raw = str(_pick(d, ["comments", "comment_count", "commentCount", "评论", "评论数"], ""))
        note_id = str(_pick(d, ["note_id", "noteId", "id"], ""))
        body = str(_pick(d, ["body", "desc", "description", "content", "text", "正文"], ""))
        cover = _pick(d, ["cover_url", "cover", "image", "image_url", "first_image"], "")
        if isinstance(cover, dict):
            cover = cover.get("url") or cover.get("urlDefault") or ""
        if isinstance(cover, list):
            cover = cover[0] if cover else ""
            if isinstance(cover, dict):
                cover = cover.get("url") or cover.get("urlDefault") or ""
        cover = str(cover or "")
        if content_type=="unknown": content_type,content_type_source=detect_content_type(d)
        if content_type_source!="unknown": content_type_source="detail:"+content_type_source

        # likes 详情为空时保留 search 的原值；收藏/评论为空则记 0。
        current = con.execute(
            "SELECT MAX(likes_raw), MAX(likes_num) FROM samples WHERE collected=? AND url=?",
            (collected, url),
        ).fetchone()
        if not likes_raw:
            likes_raw = str((current or [""])[0] or "")
        likes_num = parse_count(likes_raw) if likes_raw else int((current or [0, 0])[1] or 0)

        con.execute(
            """
            UPDATE samples
            SET likes_raw=?, likes_num=?,
                collects_raw=?, collects_num=?, comments_raw=?, comments_num=?,
                note_id=CASE WHEN ?!='' THEN ? ELSE note_id END,
                body=CASE WHEN ?!='' THEN ? ELSE body END,
                cover_url=CASE WHEN ?!='' THEN ? ELSE cover_url END,
                content_type=CASE WHEN ?!='unknown' THEN ? ELSE content_type END,
                content_type_source=CASE WHEN ?!='unknown' THEN ? ELSE content_type_source END,
                detail_json=?, detail_fetched=1, detail_collected=?
            WHERE collected=? AND url=?
            """,
            (
                likes_raw, likes_num,
                collects_raw, parse_count(collects_raw),
                comments_raw, parse_count(comments_raw),
                note_id, note_id, body, body, cover, cover,
                content_type,content_type,content_type_source,content_type_source,
                json.dumps(d, ensure_ascii=False),
                date.today().isoformat(), collected, url,
            ),
        )
        con.commit()
        enriched += 1

    con.close()
    return {"enriched":enriched,"attempted":attempted,"selected":len(rows),"failed":failed,
            "hard_stop":hard_stop,"skipped":""}


def collect_samples(keywords=None, limit=20, detail_top_n=5, cache_hours=8) -> dict:
    blocked = collect.operation_policy.blocked_result("platform_search")
    if blocked:
        return {"ok":False,"total":0,"detail_enriched":0,"failed":[],
                "operation_policy":blocked["operation_policy"],"error":blocked["error"]}
    keywords = keywords or KEYWORDS
    con = init_db()
    today = date.today().isoformat()
    total, failed, cached = [], [], []
    fresh_urls: set[str] = set()
    dom_fallback_keywords: list[str] = []
    prefer_dom = False
    dom_session_used = False
    hard_stop = None
    keyword_failures: list[dict] = []

    for i, kw in enumerate(keywords):
        fresh, cached_n = _keyword_cache_fresh(con, kw, cache_hours=cache_hours)
        if fresh:
            cached.append((kw, cached_n))
            print(f"  {kw}: 使用缓存({cached_n}条, {cache_hours}h内不重复抓)")
            continue

        if i > 0:
            time.sleep(5 + random.uniform(0, 3))
        if prefer_dom:
            dom_session_used = True
            data, err = search_via_browser_dom(kw, limit=limit)
            if not err:
                dom_fallback_keywords.append(kw)
        else:
            try:
                # 先试官方 adapter；遇到 EMPTY_RESULT 后本轮剩余关键词直接走 DOM，避免重复失败请求。
                data, err = collect.run_cli(["search", kw, "--limit", str(limit)], retries=0)
            except RuntimeError as e:
                err = {"code": "RUNTIME", "message": str(e)}
                data = None

            adapter_empty = (
                (err and str(err.get("code", "")).upper() in {
                    "EMPTY_RESULT", "OUTPUT_NOT_JSON", "CLI_EXEC_ERROR", "CLI_ARGUMENT_ERROR", "EMPTY_OUTPUT"
                })
                or (not err and (data == [] or (isinstance(data, dict) and data.get("results") == [])))
            )
            if adapter_empty:
                prefer_dom = True
                dom_session_used = True
                adapter_code = str((err or {}).get("code") or "EMPTY_RESULT")
                print(f"  {kw}: OpenCLI search {adapter_code}，切换 Browser DOM 兜底")
                data, err = search_via_browser_dom(kw, limit=limit)
                if not err:
                    dom_fallback_keywords.append(kw)
        if err:
            failed.append((kw, err.get("message", str(err))[:120]))
            keyword_failures.append({
                "keyword": kw,
                "code": str(err.get("code") or "ERROR"),
                "message": str(err.get("message") or err)[:300],
            })
            # DOM 也为空时继续打后续关键词没有价值；明确停止而非假装“0 条正常”。
            if _must_stop_batch(err) or str(err.get("code", "")).upper().startswith("DOM_"):
                hard_stop = {
                    "keyword": kw,
                    "code": err.get("code", ""),
                    "message": str(err.get("message", ""))[:240],
                    "manual_verification_required": bool(err.get("manual_verification_required")),
                    "session": str(err.get("session") or ""),
                    "page_url": str(err.get("page_url") or ""),
                }
                print(f"  {kw}: 触发停止条件 {hard_stop['code']}，本轮后续关键词不再请求")
                break
            continue

        results = data if isinstance(data, list) else (data or {}).get("results", [])
        for r in results:
            _upsert_search_result(con, today, kw, r)
            total.append(r)
            if r.get("url"):
                fresh_urls.add(str(r["url"]))
        con.commit()
        source = "Browser DOM" if kw in dom_fallback_keywords else "OpenCLI adapter"
        print(f"  {kw}: {len(results)} 帖 ({source})")

    # 一键流程短时间重跑时关键词会命中缓存；此时仍应重试“当日、缓存期内、
    # 尚未补全”的signed URL，否则解析器修好后也会因为fresh_urls为空而得到0/0。
    recent_cutoff = (datetime.now() - timedelta(hours=cache_hours)).isoformat(timespec="seconds")
    recent_pending_urls = {
        str(row[0]) for row in con.execute(
            """
            SELECT DISTINCT url FROM samples
            WHERE collected=? AND COALESCE(detail_fetched,0)=0
              AND url LIKE '%xsec_token=%' AND collected_at>=?
            """,
            (today, recent_cutoff),
        ).fetchall() if row[0]
    }
    detail_candidate_urls = fresh_urls | recent_pending_urls
    con.close()
    manual_verification = bool(hard_stop and hard_stop.get("manual_verification_required"))
    if dom_session_used and not manual_verification:
        _close_dom_session()

    detail = {"enriched":0,"attempted":0,"selected":0,"failed":[],"hard_stop":None,
              "skipped":"因搜索阶段已停止，未请求详情" if hard_stop else ""}
    # 已经触发限流/验证时，本轮不继续详情请求。
    if detail_top_n and not hard_stop:
        # 允许重试缓存期内的当日signed URL；若单条token过期，只跳过该篇。
        detail = enrich_top_samples(today, top_n=detail_top_n, candidate_urls=detail_candidate_urls)
        print(f"  详情补全: {detail['enriched']}/{detail['attempted']} 条")

    count_con=init_db()
    media_counts={str(k or "unknown"):int(v or 0) for k,v in count_con.execute(
        "SELECT COALESCE(content_type,'unknown'),COUNT(*) FROM samples WHERE collected=? GROUP BY content_type",
        (today,)).fetchall()}
    count_con.close()

    return {
        "ok": hard_stop is None,
        "total": len(total),
        "keywords": len(keywords),
        "cached_keywords": len(cached),
        "cached": cached,
        "search_source": "browser_dom_fallback" if dom_fallback_keywords else "opencli_adapter",
        "dom_fallback_keywords": dom_fallback_keywords,
        "failed": failed,
        "keyword_failures": keyword_failures,
        "hard_stop": hard_stop,
        "manual_verification_required": manual_verification,
        "verification_session": DOM_SEARCH_SESSION if manual_verification else "",
        "detail_enriched": detail["enriched"],
        "detail_attempted": detail["attempted"],
        "detail_selected": detail.get("selected", detail["attempted"]),
        "detail_failed": detail["failed"],
        "detail_hard_stop": detail.get("hard_stop"),
        "detail_skipped_reason": detail.get("skipped", ""),
        "fresh_signed_urls": len([u for u in fresh_urls if "xsec_token=" in u]),
        "detail_candidate_urls": len([u for u in detail_candidate_urls if "xsec_token=" in u]),
        "media_counts":media_counts,
    }


def main():
    print(f"[样本采集] 关键词 {len(KEYWORDS)} 个 …")
    r = collect_samples()
    if not r.get("ok", True):
        print("[运行策略] " + r.get("error", "已阻止平台搜索"), file=sys.stderr)
        return
    print(f"[完成] 入库 {r['total']} 条,详情补全 {r['detail_enriched']} 条,失败 {len(r['failed'])} 个关键词")
    if r["failed"]:
        for kw, msg in r["failed"]:
            print(f"  失败 {kw}: {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
