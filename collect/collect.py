#!/usr/bin/env python3
"""L2 数据采集 —— 全部通过小红书 CLI (opencli xiaohongshu)。

拉取:账号总览 / 逐篇摘要,存原始 JSON 到 data/raw/,关键指标入 SQLite。

字段结构(2026-08 实测校准):
  creator-stats  -> [{"metric":"观看数 (views)","total":0,"trend":"0 → 0 → ..."}, ...]
  creator-notes-summary -> 列表;账号无笔记时返回 {"ok":false,"error":{"code":"EMPTY_RESULT"}}

登录态:opencli 通过 Chrome 扩展复用真实浏览器会话。
  - 创作者数据 需登录 creator.xiaohongshu.com
  - search 需登录 www.xiaohongshu.com(独立登录墙)
"""
import json
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import yaml
from datetime import date, datetime
from pathlib import Path


# Windows 控制台/cron 下默认 GBK,强制 stdout/stderr 用 UTF-8 打印中文
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from content import attribution
from analyze import account_intelligence
from compliance import operation_policy

RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "metrics.db"

# Windows 下 opencli 是 npm 的 .cmd 包装脚本,subprocess 需解析出真实路径
OPENCLI = shutil.which("opencli") or "opencli"

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TOKEN_RE = re.compile(r"(?i)(xsec_token=)[^&\s\"']+")


def _clean_cli_text(value: str) -> str:
    """去除ANSI控制码并统一换行，便于从普通文本错误中识别真实原因。"""
    text = ANSI_RE.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _safe_preview(value: str, limit: int = 420) -> str:
    """日志预览必须隐藏signed URL中的token。"""
    text = TOKEN_RE.sub(r"\1<redacted>", _clean_cli_text(value))
    return text[:limit].replace("\n", " | ")


def _classify_text_error(stdout: str, stderr: str, exit_code: int) -> dict:
    """把OpenCLI的普通文本/ANSI错误恢复成稳定错误码。"""
    out = _clean_cli_text(stdout)
    err = _clean_cli_text(stderr)
    blob = (out + "\n" + err).strip()
    low = blob.lower()
    preview = _safe_preview(blob) or f"OpenCLI exit={exit_code}，无可读输出"

    if any(k in low for k in ("security_block", "security block", "security check", "security verification",
                               "安全验证", "安全限制", "访问链接异常", "验证码", "captcha", "风控", "请求频繁")):
        return {"code": "SECURITY_CHECK", "message": preview, "exit_code": exit_code}
    if any(k in low for k in ("429", "rate_limited", "rate limit", "too many requests", "限流", "操作频繁")):
        return {"code": "RATE_LIMITED", "message": preview, "exit_code": exit_code}
    if any(k in low for k in ("auth_required", "login required", "not logged in", "please login", "请先登录",
                               "登录后查看", "扫码登录", "401 unauthorized", "unauthorized")):
        return {"code": "AUTH", "message": preview, "exit_code": exit_code}
    if "empty_result" in low or "no results" in low or "未找到结果" in low:
        return {"code": "EMPTY_RESULT", "message": preview, "exit_code": exit_code}
    signed_problem = any(k in low for k in ("signed url", "full signed", "bare note", "tokenless")) or (
        "xsec_token" in low and any(k in low for k in ("required", "missing", "need ", "必须", "缺少", "需要"))
    )
    if signed_problem:
        return {"code": "SIGNED_URL_REQUIRED", "message": preview, "exit_code": exit_code}
    if any(k in low for k in ("unknown option", "invalid option", "unknown command", "missing required")):
        return {"code": "CLI_ARGUMENT_ERROR", "message": preview, "exit_code": exit_code}
    return {
        "code": "OUTPUT_NOT_JSON" if exit_code == 0 else "CLI_EXEC_ERROR",
        "message": preview,
        "exit_code": exit_code,
    }


def _canonical_error(err: dict) -> dict:
    """统一OpenCLI不同版本的近义错误码。"""
    value = dict(err or {})
    code = str(value.get("code") or "ERROR").upper()
    if "AUTH" in code or "LOGIN" in code:
        value["code"] = "AUTH"
    elif "EMPTY" in code or "NO_RESULT" in code:
        value["code"] = "EMPTY_RESULT"
    elif "SIGNED" in code or "XSEC" in code:
        value["code"] = "SIGNED_URL_REQUIRED"
    elif "RATE" in code or code == "429":
        value["code"] = "RATE_LIMITED"
    return value




def _hard_stop_error(err: dict | None, stderr_blob: str = "") -> dict | None:
    """429/验证码/风控属于硬停止：不要自动连续重试。"""
    msg = (str((err or {}).get("code", "")) + " " + str((err or {}).get("message", ""))
           + " " + str((err or {}).get("help", "")) + " " + (stderr_blob or "")).lower()
    if any(k in msg for k in ("429", "rate limit", "rate_limited", "too many", "频繁", "限流")):
        return {"code": "RATE_LIMITED", "message": str((err or {}).get("message") or stderr_blob or "请求频率受限")}
    if any(k in msg for k in ("验证码", "安全验证", "安全限制", "风控", "captcha", "verify", "verification", "security block", "security_block")):
        return {"code": "SECURITY_CHECK", "message": str((err or {}).get("message") or stderr_blob or "触发安全验证")}
    return None

def _is_rate_limited(err: dict | None, stderr_blob: str = "") -> bool:
    """判断错误是否为限流(rate-limit / 频率过高 / 空输出)。"""
    if not err:
        return False
    msg = (str(err.get("code", "")) + " " + str(err.get("message", ""))).lower()
    blob = stderr_blob.lower()
    for kw in ("429", "频繁", "稍后", "rate", "限流", "rate_limited", "too many", "empty output"):
        if kw in msg or kw in blob:
            return True
    return False


# 默认重试参数:最大 5 次, base 2s, 最大等待 120s
DEFAULT_RETRIES = 5
DEFAULT_BASE_DELAY = 2.0
DEFAULT_MAX_DELAY = 120.0

# 限流(RATE_LIMITED)策略:对齐 XHS「1分钟后再尝试」,等 65s(含 5s 余量)重试一次
RATE_LIMIT_RETRY_WAIT = 65.0


def _decode_json_output(out: str):
    """解析 OpenCLI stdout。允许 JSON 前后混有提示/日志/BOM。

    OpenCLI/Browser Bridge 某些版本会在 stdout 里夹一行提示；旧代码直接
    json.loads(out) 会抛 JSONDecodeError，导致整条 pipeline 中断。
    """
    text = _clean_cli_text(out).lstrip("\ufeff").strip()
    if not text:
        raise ValueError("empty stdout")

    # 最常见：stdout 本身就是纯 JSON。
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 兼容：提示文本 + JSON + 尾部日志。扫描每个可能的 JSON 起点，
    # raw_decode 只消费第一个完整 JSON，不要求后面也为空。
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("stdout contains no valid JSON")


def _normalize_field_value_rows(value):
    """把 OpenCLI note 的 YAML 字段列表还原成普通详情对象。

    部分适配器即使指定 ``-f json``，仍返回：
    ``[{field: title, value: ...}, {field: content, value: ...}]`` 的 YAML。
    这本身是可用数据，不应被当成 CLI 执行失败。
    """
    if not isinstance(value, list) or not value:
        return value
    if not all(isinstance(row, dict) and "field" in row and "value" in row for row in value):
        return value
    result = {}
    for row in value:
        field = str(row.get("field") or "").strip()
        if field:
            result[field] = row.get("value")
    return result or value


def _decode_cli_output(out: str):
    """解析 OpenCLI 的 JSON，兼容 note 适配器实际返回的 YAML。"""
    try:
        return _normalize_field_value_rows(_decode_json_output(out))
    except ValueError:
        pass

    text = _clean_cli_text(out).lstrip("\ufeff").strip()
    if not text:
        raise ValueError("empty stdout")

    candidates = [text]
    # 兼容 YAML 前夹有一两行普通提示：从首个 ``- field:`` 开始再解析。
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*field\s*:", line, re.I):
            candidates.append("\n".join(lines[i:]))
            break
    for candidate in candidates:
        try:
            value = yaml.safe_load(candidate)
        except yaml.YAMLError:
            continue
        normalized = _normalize_field_value_rows(value)
        if isinstance(normalized, (dict, list)):
            return normalized
    raise ValueError("stdout contains no valid JSON/YAML payload")


def _looks_like_note_detail(value) -> bool:
    """判断已解析对象是否足以作为 note 详情继续入库。"""
    if not isinstance(value, dict):
        return False
    keys = {str(k).lower() for k in value}
    content_keys = {"content", "body", "desc", "description", "text", "正文"}
    metric_keys = {"likes", "like_count", "likecount", "collects", "favorites",
                   "favorite_count", "collect_count", "comments", "comment_count",
                   "commentcount", "点赞", "收藏", "评论"}
    return "title" in keys and bool(keys & (content_keys | metric_keys))


def _run_cli_once(args: list[str],
            retries: int = DEFAULT_RETRIES,
            base_delay: float = DEFAULT_BASE_DELAY,
            max_delay: float = DEFAULT_MAX_DELAY) -> tuple[object, dict | None]:
    """执行 opencli xiaohongshu 命令(带指数退避 + jitter 防雷)。

    返回 (data, error)。成功时 error=None;
    CLI 返回 {"ok":false,"error":{...}} 时 data=None, error=错误对象。
    """
    cmd = [OPENCLI, "xiaohongshu", *args, "--window", "background", "-f", "json"]
    last_err = None
    for attempt in range(retries + 1):
        if attempt > 0:
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter = random.uniform(0, delay * 0.3)  # 最多 30% 随机抖动
            wait = delay + jitter
            print(f"[rate-limit] 第{attempt}次重试,等待 {wait:.0f}s…")
            time.sleep(wait)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            last_err = {"code": "TIMEOUT", "message": f"命令超时(180s): {' '.join(cmd)}"}
            continue

        out = _clean_cli_text(proc.stdout or "")
        stderr_blob = _clean_cli_text(proc.stderr or "")

        # 空输出:可能是限流
        if not out:
            classified = _classify_text_error("", stderr_blob, proc.returncode)
            if classified["code"] not in {"OUTPUT_NOT_JSON", "CLI_EXEC_ERROR"}:
                return None, classified
            # 空输出原因不明确，不把它当成 429 连续轰炸；最多按调用方 retries 策略处理。
            last_err = {"code": "EMPTY_OUTPUT",
                        "message": _safe_preview(stderr_blob) or "OpenCLI没有返回stdout"}
            if attempt >= retries:
                return None, last_err
            continue

        data = None
        parsed_from = ""
        # 某些OpenCLI/Windows组合会把结构化错误写到stderr；两边都检查。
        for source_name, source_text in (("stdout", out), ("stderr", stderr_blob)):
            if not source_text:
                continue
            try:
                data = _decode_cli_output(source_text)
                parsed_from = source_name
                break
            except ValueError:
                continue
        if data is None:
            return None, _classify_text_error(out, stderr_blob, proc.returncode)

        # 兼容浏览器daemon的 {session,data} 以及 {ok:true,data} 包装。
        if isinstance(data, dict) and "data" in data and (
                "session" in data or data.get("ok") is True):
            data = data.get("data")
        data = _normalize_field_value_rows(data)

        if isinstance(data, dict) and (data.get("ok") is False or data.get("error")):
            err = data.get("error", {}) or {}
            if not isinstance(err, dict):
                err = {"code": "CLI_ERROR", "message": str(err)}
            err = _canonical_error(err)
            hard = _hard_stop_error(err, stderr_blob)
            if hard:
                return None, hard
            if _is_rate_limited(err, stderr_blob):
                last_err = err
                continue
            return None, err
        # 有些版本打印 `Unexpected error: {"code":...,"message":...}`，
        # raw_decode能取到内部JSON，但它本身没有ok/error外壳。
        if isinstance(data, dict) and data.get("code") and data.get("message"):
            err = _canonical_error({
                "code": str(data.get("code")),
                "message": str(data.get("message")),
                "help": str(data.get("help") or data.get("hint") or ""),
            })
            hard = _hard_stop_error(err, stderr_blob)
            if hard:
                return None, hard
            return None, err
        if proc.returncode != 0:
            # 某些 OpenCLI note 适配器已经输出完整 field/value 详情，却以非零码退出。
            # 先只检查“另一条输出流”的明确硬错误；若只是泛化退出码，则接受可用详情。
            diagnostic_out = out if parsed_from != "stdout" else ""
            diagnostic_err = stderr_blob if parsed_from != "stderr" else ""
            classified = _classify_text_error(diagnostic_out, diagnostic_err, proc.returncode)
            if (args and args[0] == "note" and _looks_like_note_detail(data)
                    and classified.get("code") in {"CLI_EXEC_ERROR", "OUTPUT_NOT_JSON"}):
                print("[OpenCLI兼容] note已返回可用field/value详情，忽略适配器非零退出码")
                return data, None
            return None, classified
        # 成功
        return data, None

    # 所有重试耗尽
    return None, last_err or {"code": "MAX_RETRIES", "message": "重试耗尽"}


def run_cli(args: list[str],
            retries: int = 0,
            base_delay: float = DEFAULT_BASE_DELAY,
            max_delay: float = DEFAULT_MAX_DELAY) -> tuple[object, dict | None]:
    """执行一次 opencli xiaohongshu 命令；验证或限流错误直接返回。"""
    blocked = operation_policy.blocked_result("platform_collect")
    if blocked:
        return None, {"code":"OPERATION_BLOCKED","message":blocked["error"],
                      "operation_policy":blocked["operation_policy"]}
    return _run_cli_once(args, retries, base_delay, max_delay)


def metric_key(metric: str) -> str:
    """从 '观看数 (views)' 提取英文 key 'views';无括号则返回原串。"""
    m = re.search(r"\(([^)]+)\)", metric or "")
    return m.group(1).strip() if m else (metric or "").strip()


def metric_number(value):
    """兼容 1,234 / 1.2万 / 3.4k；缺失返回 None，避免把缺失当真实0。"""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(",", "")
    multiplier = 10000 if text.endswith(("万", "w")) else 1000 if text.endswith("k") else 1
    if multiplier != 1:
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def duration_seconds(value):
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    try:
        if text.endswith("ms"): return float(text[:-2]) / 1000
        if text.endswith("毫秒"): return float(text[:-2]) / 1000
        if text.endswith("秒"): return float(text[:-1])
        if text.endswith("s"): return float(text[:-1])
        return float(text)
    except ValueError:
        return None


def percent_value(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace("%", "")
    try:
        number = float(text)
        return number / 100 if "%" in str(value) or number > 1 else number
    except ValueError:
        return None


def backfill_extended_metrics_from_raw(con: sqlite3.Connection) -> int:
    """把0813已经抓到但旧表没接入的逐篇字段无损回填。"""
    changed=0
    if not RAW.exists(): return 0
    for path in sorted(RAW.glob("*_notes.json")):
        collected=path.name[:10]
        try: payload=json.loads(path.read_text(encoding="utf-8"))
        except Exception: continue
        rows=payload if isinstance(payload,list) else payload.get("notes",[]) if isinstance(payload,dict) else []
        for item in rows:
            if not isinstance(item,dict): continue
            note_id=str(item.get("note_id") or item.get("noteId") or item.get("id") or "")
            title=str(item.get("title") or "")
            if not note_id and not title: continue
            existing=con.execute("SELECT topic_id FROM note_metrics WHERE note_id=? AND collected=?",(note_id,collected)).fetchone()
            topic_id=str(existing[0] if existing and existing[0] else attribution.resolve_topic(con,note_id,title) or "")
            values=(note_id,collected,title,int(metric_number(item.get("views")) or 0),
                    int(metric_number(item.get("likes")) or 0),int(metric_number(item.get("collects") or item.get("favorites")) or 0),
                    int(metric_number(item.get("comments")) or 0),topic_id,str(item.get("published_at") or ""),
                    int(metric_number(item.get("shares")) or 0),duration_seconds(item.get("avg_view_time")),
                    int(metric_number(item.get("rise_fans")) or 0),str(item.get("top_source") or ""),
                    percent_value(item.get("top_source_pct")),str(item.get("top_interest") or ""),
                    percent_value(item.get("top_interest_pct")),metric_number(item.get("impressions")),
                    metric_number(item.get("clicks")),percent_value(item.get("ctr")),
                    percent_value(item.get("completion_rate")),int(metric_number(item.get("home_views")) or 0),
                    json.dumps(item,ensure_ascii=False))
            con.execute("""INSERT INTO note_metrics(note_id,collected,title,views,likes,favorites,comments,topic_id,
              published_at,shares,avg_view_time_seconds,rise_fans,top_source,top_source_pct,top_interest,top_interest_pct,
              impressions,clicks,ctr,completion_rate,home_views,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(note_id,collected) DO UPDATE SET published_at=excluded.published_at,shares=excluded.shares,
              avg_view_time_seconds=excluded.avg_view_time_seconds,rise_fans=excluded.rise_fans,
              top_source=excluded.top_source,top_source_pct=excluded.top_source_pct,
              top_interest=excluded.top_interest,top_interest_pct=excluded.top_interest_pct,
              impressions=COALESCE(excluded.impressions,note_metrics.impressions),
              clicks=COALESCE(excluded.clicks,note_metrics.clicks),ctr=COALESCE(excluded.ctr,note_metrics.ctr),
              completion_rate=COALESCE(excluded.completion_rate,note_metrics.completion_rate),
              home_views=excluded.home_views,raw_json=excluded.raw_json""",values)
            changed+=1
    con.commit(); return changed


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS note_metrics (
            note_id TEXT, collected TEXT, title TEXT,
            views INTEGER, likes INTEGER, favorites INTEGER, comments INTEGER,
            topic_id TEXT, PRIMARY KEY (note_id, collected)
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS account_stats (
            collected TEXT, metric TEXT, total INTEGER, trend TEXT,
            PRIMARY KEY (collected, metric)
        )""")
    attribution.init_db(con)
    account_intelligence.ensure_schema(con)
    backfill_extended_metrics_from_raw(con)
    con.commit()
    return con


def main():
    blocked = operation_policy.blocked_result("platform_collect")
    if blocked:
        print("[运行策略] " + blocked["error"], file=sys.stderr)
        return
    today = date.today().isoformat()
    RAW.mkdir(parents=True, exist_ok=True)
    con = init_db()
    attribution.bootstrap_local_drafts(con, ROOT / "content" / "drafts")

    # 1) 官方创作者数据中心优先；CLI脚本运行时用文件路径加载同目录适配器，
    # 避免 ``python collect/collect.py`` 下包名与当前文件冲突。
    import importlib.util as _ilu
    _dc_spec = _ilu.spec_from_file_location("_creator_data_center", Path(__file__).with_name("creator_data_center.py"))
    _dc = _ilu.module_from_spec(_dc_spec)
    _dc_spec.loader.exec_module(_dc)
    data_center = _dc.collect_account_data()
    dc_metrics = data_center.get("account_metrics", {}) if data_center.get("ok") else {}
    if dc_metrics:
        stats = []
        for key, value in dc_metrics.items():
            evidence = (data_center.get("metric_evidence") or {}).get(key, {})
            stats.append({"metric": f"{evidence.get('label') or key} ({key})",
                          "total": value, "trend": "创作者数据中心"})
        err = None
        account_intelligence.save_data_center_snapshot(con, data_center)
        if data_center.get("observed_audience") or data_center.get("active_periods"):
            account_intelligence.save_audience_snapshot(con, {
                "observed": data_center.get("observed_audience") or {},
                "active_periods": [{"window": x} if isinstance(x, str) else x
                                   for x in (data_center.get("active_periods") or [])],
                "confidence": .95,
                "basis": "小红书创作者数据中心可见DOM",
            }, "creator_data_center_dom")
        print(f"[创作者数据中心] {len(dc_metrics)} 项真实指标已读取；{data_center.get('raw_path','')}")
    else:
        dc_error = data_center.get("error") or {}
        print(f"[数据中心兜底] {dc_error.get('message') or data_center.get('warning') or '页面暂无可读指标'}", file=sys.stderr)
        stats, err = run_cli(["creator-stats", "--period", "thirty"])
    if err:
        if "401" in str(err.get("message", "")):
            print("[登录态失效] 请登录 creator.xiaohongshu.com 后重试。", file=sys.stderr)
            sys.exit(2)
        print(f"[警告] creator-stats: {err.get('message')}", file=sys.stderr)
        stats = []
    (RAW / f"{today}_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in (stats or []):
        con.execute("INSERT OR REPLACE INTO account_stats VALUES (?,?,?,?)",
                    (today, metric_key(row.get("metric", "")),
                     metric_number(row.get("total")) or 0, str(row.get("trend", ""))))
    con.commit()
    print(f"[账号总览] {len([r for r in (stats or [])])} 项指标已入库")

    # 2) 逐篇摘要 —— 账号无笔记时 EMPTY_RESULT,属正常,不中止
    summary, err = run_cli(["creator-notes-summary"])
    if err:
        code = err.get("code", "")
        if code == "EMPTY_RESULT":
            if data_center.get("notes"):
                summary = []
                print("[逐篇数据] creator-notes-summary为空，继续使用数据中心逐篇表格。")
            else:
                print("[逐篇数据] 账号暂无已发布笔记 —— 闭环的内容侧数据待首篇发布后产生。")
                con.close()
                return
        if "401" in str(err.get("message", "")):
            print("[登录态失效] 请登录 creator.xiaohongshu.com 后重试。", file=sys.stderr)
            sys.exit(2)
        if data_center.get("notes"):
            summary = []
            print(f"[逐篇数据兜底] {err.get('message')}；使用数据中心表格。", file=sys.stderr)
        else:
            print(f"[警告] creator-notes-summary: {err.get('message')}", file=sys.stderr)
            con.close()
            return

    (RAW / f"{today}_notes.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_notes = summary if isinstance(summary, list) else summary.get("notes", [])
    merged_notes = {}
    for item in [*(data_center.get("notes") or []), *(summary_notes or [])]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("title") or item.get("标题") or item.get("note_id") or item.get("id") or "").strip()
        if key:
            merged_notes[key] = {**merged_notes.get(key, {}), **item}
    notes = list(merged_notes.values())
    KEY_MAP = {  # 兼容 creator-notes-summary 已观测字段与未来扩展字段
        "note_id": ["note_id", "id", "noteId"],
        "title": ["title", "标题"],
        "views": ["views", "view", "观看数", "观看"],
        "likes": ["likes", "like", "点赞数", "点赞"],
        "favorites": ["favorites", "collects", "collect", "收藏数", "收藏"],
        "comments": ["comments", "comment", "评论数", "评论"],
        "shares": ["shares", "share", "分享数", "分享"],
        "rise_fans": ["rise_fans", "new_followers", "followers_gained", "涨粉数", "涨粉"],
        "avg_view_time": ["avg_view_time", "average_view_time", "平均观看时长"],
        "published_at": ["published_at", "publish_time", "发布时间"],
        "top_source": ["top_source", "流量来源"],
        "top_source_pct": ["top_source_pct", "流量来源占比"],
        "top_interest": ["top_interest", "兴趣标签"],
        "top_interest_pct": ["top_interest_pct", "兴趣占比"],
        "impressions": ["impressions", "exposures", "曝光数", "曝光"],
        "clicks": ["clicks", "entries", "进入数", "点击数"],
        "ctr": ["ctr", "entry_rate", "进入率", "点击率"],
        "completion_rate": ["completion_rate", "完播率", "阅读完成率"],
        "home_views": ["home_views", "profile_views", "主页访问"],
    }

    def pick(d, keys, default=0):
        for k in keys:
            if k in d:
                return d[k]
        return default

    rows = 0
    mapped = 0
    for n in notes:
        note_id = str(pick(n, KEY_MAP["note_id"], ""))
        title = str(pick(n, KEY_MAP["title"], ""))
        topic_id = str(n.get("topic_id") or attribution.resolve_topic(con, note_id, title) or "")
        if topic_id:
            mapped += 1
            attribution.mark_published(con, note_id, title, topic_id)
        values = {
            "views": int(metric_number(pick(n, KEY_MAP["views"])) or 0),
            "likes": int(metric_number(pick(n, KEY_MAP["likes"])) or 0),
            "favorites": int(metric_number(pick(n, KEY_MAP["favorites"])) or 0),
            "comments": int(metric_number(pick(n, KEY_MAP["comments"])) or 0),
            "shares": int(metric_number(pick(n, KEY_MAP["shares"])) or 0),
            "rise_fans": int(metric_number(pick(n, KEY_MAP["rise_fans"])) or 0),
            "avg_view_time_seconds": duration_seconds(pick(n, KEY_MAP["avg_view_time"], None)),
            "published_at": str(pick(n, KEY_MAP["published_at"], "") or ""),
            "top_source": str(pick(n, KEY_MAP["top_source"], "") or ""),
            "top_source_pct": percent_value(pick(n, KEY_MAP["top_source_pct"], None)),
            "top_interest": str(pick(n, KEY_MAP["top_interest"], "") or ""),
            "top_interest_pct": percent_value(pick(n, KEY_MAP["top_interest_pct"], None)),
            "impressions": metric_number(pick(n, KEY_MAP["impressions"], None)),
            "clicks": metric_number(pick(n, KEY_MAP["clicks"], None)),
            "ctr": percent_value(pick(n, KEY_MAP["ctr"], None)),
            "completion_rate": percent_value(pick(n, KEY_MAP["completion_rate"], None)),
            "home_views": int(metric_number(pick(n, KEY_MAP["home_views"])) or 0),
        }
        con.execute("""INSERT OR REPLACE INTO note_metrics(
            note_id,collected,title,views,likes,favorites,comments,topic_id,published_at,
            shares,avg_view_time_seconds,rise_fans,top_source,top_source_pct,top_interest,
            top_interest_pct,impressions,clicks,ctr,completion_rate,home_views,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (note_id,today,title,values["views"],values["likes"],values["favorites"],values["comments"],topic_id,
             values["published_at"],values["shares"],values["avg_view_time_seconds"],values["rise_fans"],
             values["top_source"],values["top_source_pct"],values["top_interest"],values["top_interest_pct"],
             values["impressions"],values["clicks"],values["ctr"],values["completion_rate"],values["home_views"],
             json.dumps(n,ensure_ascii=False)))
        snapshot_at=datetime.now().isoformat(timespec="seconds")
        con.execute("""INSERT OR REPLACE INTO note_metric_snapshots(
            note_id,collected_at,title,topic_id,published_at,views,likes,favorites,comments,shares,
            avg_view_time_seconds,rise_fans,top_source,top_source_pct,top_interest,top_interest_pct,
            impressions,clicks,ctr,completion_rate,home_views,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (note_id,snapshot_at,title,topic_id,values["published_at"],values["views"],values["likes"],
             values["favorites"],values["comments"],values["shares"],values["avg_view_time_seconds"],
             values["rise_fans"],values["top_source"],values["top_source_pct"],values["top_interest"],
             values["top_interest_pct"],values["impressions"],values["clicks"],values["ctr"],
             values["completion_rate"],values["home_views"],json.dumps(n,ensure_ascii=False)))
        rows += 1
    con.commit()
    con.close()
    print(f"[采集完成] {today}: {rows} 篇笔记数据已入库 {DB}；自动归因 {mapped} 篇")


if __name__ == "__main__":
    main()
