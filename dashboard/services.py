"""业务封装 —— 把现有脚本的复用函数编排成看板要的操作。

零改动 import 现有 content/analyze/collect/make_cover 模块(借位 ROOT)。
慢函数(search/generate/publish)由 app.py 丢进 tasks.submit 后台跑。
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import date, datetime
import time
from pathlib import Path
import uuid

OPENCLI = shutil.which("opencli") or "opencli"

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content import generate, make_cover, attribution   # noqa: E402
from content.carousel import generate_carousel           # noqa: E402
from dashboard.xhs_ui_publish import push_to_xhs_draft    # noqa: E402
from analyze import analyze, heat, pipeline, account_intelligence        # noqa: E402
from collect import collect, sample_collect, creator_data_center          # noqa: E402
from golden import service as golden_service           # noqa: E402
from golden import repository as golden_repository       # noqa: E402
from golden import retriever as golden_retriever          # noqa: E402
from agents.topic import agent as topic_agent             # noqa: E402
from agents.content import agent as content_agent         # noqa: E402
from agents.cover import agent as cover_agent             # noqa: E402
from agents.orchestrator import agent as orchestrator_agent # noqa: E402
from agents.reviewer import agent as reviewer_agent         # noqa: E402
from compliance import policy as compliance_policy           # noqa: E402
from compliance import operation_policy                      # noqa: E402
from personal_ops import workflow as personal_workflow       # noqa: E402
from canva_connect import CanvaClient, CanvaError             # noqa: E402
from content.carousel.pptx_builder import ensure_editable_pptx # noqa: E402

DRAFTS = ROOT / "content" / "drafts"
COVERS = ROOT / "content" / "covers"
DB = ROOT / "data" / "metrics.db"


def generate_visual_carousel(topic_id: str, content: dict, model_config: dict | None = None) -> dict:
    """Create a review-ready carousel, optionally with native Codex CLI visuals."""
    weights = analyze.load_weights()
    topic = next((item for item in weights.get("topics", []) if item.get("id") == topic_id), None)
    if not topic:
        topic = {"id": topic_id or "manual", "label": content.get("title", "健身图文")}
    return generate_carousel.generate(topic, content, generate.load("persona.yaml"), model_config or {})


def select_carousel_cover(post_id: str, index: int) -> dict:
    """Persist a human-selected carousel cover as page 1 and in the Canva handoff."""
    post_id=str(post_id or "").strip()
    if not post_id or Path(post_id).name!=post_id or not post_id.startswith("xhs_"):
        return {"ok":False,"error":"图文包编号无效"}
    try:
        selected=int(index)
    except (TypeError,ValueError):
        return {"ok":False,"error":"封面序号无效"}
    if selected not in (0,1,2):
        return {"ok":False,"error":"封面序号必须为 0、1 或 2"}

    post_dir=ROOT/"content"/"post_pages"/post_id
    source=post_dir/f"cover_variant_{selected+1:02d}.jpg"
    package_file=post_dir/"carousel_package.json"
    if not source.is_file() or not package_file.is_file():
        return {"ok":False,"error":"找不到这套图文包或封面文件"}

    data=json.loads(package_file.read_text(encoding="utf-8"))
    tasks=[row for row in data.get("asset_plan",{}).get("asset_tasks",[]) if row.get("type")=="cover_variant"]
    directions=[row for row in data.get("cover_directions",[]) if isinstance(row,dict)]
    task=tasks[selected] if selected<len(tasks) else {}
    direction_id=str(task.get("cover_direction_id") or "")
    direction=next((row for row in directions if str(row.get("id") or "")==direction_id),
                   directions[selected] if selected<len(directions) else {})

    shutil.copy2(source,post_dir/"page_01.jpg")
    canva_dir=ROOT/"content"/"canva_packages"/post_id
    if canva_dir.is_dir():
        shutil.copy2(source,canva_dir/"page_01.jpg")
        manifest_file=canva_dir/"manifest.json"
        if manifest_file.is_file():
            manifest=json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest["selected_cover_index"]=selected
            manifest["selected_cover_direction_id"]=direction.get("id")
            manifest_file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")

    plan=data.setdefault("asset_plan",{})
    plan["selected_cover_index"]=selected
    plan["selected_cover_asset_id"]=task.get("asset_id")
    data["selected_cover_direction"]=direction
    data["selected_cover_index"]=selected
    package_file.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"ok":True,"post_id":post_id,"selected_cover_index":selected,
            "direction":direction,"image_path":str((post_dir/"page_01.jpg").resolve()),
            "canva_page_path":str((canva_dir/"page_01.jpg").resolve()) if canva_dir.is_dir() else ""}


def canva_status() -> dict:
    """Return connection state without exposing credentials or OAuth tokens."""
    try:
        return CanvaClient().status()
    except CanvaError as exc:
        return {"ok": True, "configured": False, "authenticated": False, "error": str(exc)}


def canva_login() -> dict:
    """Run one operator-triggered OAuth PKCE flow using the local callback."""
    return CanvaClient().login(open_browser=True, timeout=300)


def canva_import(post_id: str) -> dict:
    """Build the selected layered PPTX and import it into the operator's Canva."""
    post_id = str(post_id or "").strip()
    if not post_id or Path(post_id).name != post_id or not post_id.startswith("xhs_"):
        raise CanvaError("图文包编号无效")
    package_dir = ROOT / "content" / "canva_packages" / post_id
    if not package_dir.is_dir():
        raise CanvaError("找不到这套 Canva 图文包")
    deck = ensure_editable_pptx(package_dir, force=True)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pages = manifest.get("pages") or []
    title = str((pages[0] if pages else {}).get("title") or post_id)
    result = CanvaClient().import_design(deck, title=title)
    designs = result.get("designs") or []
    if designs:
        first = designs[0]
        manifest["canva_import"] = {
            "status": "success",
            "design_id": first.get("id"),
            "title": first.get("title"),
            "page_count": first.get("page_count"),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        }
        # Temporary edit/view URLs are returned to the browser but deliberately
        # omitted from the package so they are never included in a ZIP.
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    result["post_id"] = post_id
    return result


# ---------- 数据面板 ----------
def _derived_account_rows(con: sqlite3.Connection) -> list[dict]:
    """creator-stats 失灵时，用最新一批逐篇数据汇总账号核心指标。"""
    row = con.execute(
        """
        SELECT COUNT(DISTINCT note_id) AS notes,
               COALESCE(SUM(views), 0) AS views,
               COALESCE(SUM(likes), 0) AS likes,
               COALESCE(SUM(favorites), 0) AS favorites,
               COALESCE(SUM(comments), 0) AS comments
        FROM note_metrics
        WHERE collected=(SELECT MAX(collected) FROM note_metrics)
        """
    ).fetchone()
    if not row or int(row[0] or 0) == 0:
        return []
    return [
        {"metric": "notes", "total": int(row[0] or 0), "trend": "逐篇汇总"},
        {"metric": "views", "total": int(row[1] or 0), "trend": "逐篇汇总"},
        {"metric": "likes", "total": int(row[2] or 0), "trend": "逐篇汇总"},
        {"metric": "favorites", "total": int(row[3] or 0), "trend": "逐篇汇总"},
        {"metric": "comments", "total": int(row[4] or 0), "trend": "逐篇汇总"},
    ]


def dashboard_data() -> dict:
    """账号总览 + 逐篇数据 + 各选题权重。"""
    notes, account = [], []
    if DB.exists():
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        try:
            account_intelligence.ensure_schema(con)
            account = [dict(r) for r in con.execute(
                "SELECT metric,total,trend FROM account_stats WHERE collected=("
                "SELECT MAX(collected) FROM account_stats)").fetchall()]
            # metrics.db保留每日快照用于趋势分析；主看板每篇笔记只展示最新快照，
            # 避免同一标题昨天/今天重复两行、指标却不同。
            notes = [dict(r) for r in con.execute(
                """
                WITH ranked AS (
                    SELECT note_id,title,views,likes,favorites,comments,topic_id,collected,
                           published_at,shares,avg_view_time_seconds,rise_fans,top_source,
                           top_source_pct,top_interest,top_interest_pct,impressions,clicks,ctr,
                           completion_rate,home_views,
                           ROW_NUMBER() OVER (
                               PARTITION BY CASE
                                   WHEN COALESCE(note_id,'')!='' THEN 'id:'||note_id
                                   ELSE 'title:'||COALESCE(title,'')
                               END
                               ORDER BY collected DESC
                           ) AS rn
                    FROM note_metrics
                )
                SELECT note_id,title,views,likes,favorites,comments,topic_id,collected,published_at,
                       shares,avg_view_time_seconds,rise_fans,top_source,top_source_pct,top_interest,
                       top_interest_pct,impressions,clicks,ctr,completion_rate,home_views
                FROM ranked WHERE rn=1
                ORDER BY views DESC, collected DESC
                LIMIT 50
                """).fetchall()]

            # OpenCLI 某些版本 creator-stats 会成功返回但 total 全为 0。
            # 此时不要把“假 0”展示给看板，改用逐篇数据汇总。
            if not account or all(int(r.get("total") or 0) == 0 for r in account):
                account = _derived_account_rows(con)
        finally:
            con.close()
    weights = analyze.load_weights()
    score_committed=analyze.has_committed_score(weights)
    account_report={"topics":[],"audience":{},"timing":{},"opportunities":[]}
    if DB.exists():
        con=sqlite3.connect(DB)
        try:
            account_report=account_intelligence.report(con,weights.get("topics",[]))
        finally: con.close()
    # 只读取最近一次完整Pipeline原子提交的正式快照。页面加载、刷新样本、
    # Golden分析和推荐刷新都不得现场重算或改写柱状图。
    topics = [{"id": t["id"], "label": t["label"], "category": t["category"],
               "weight": t.get("final_score", t["weight"]),
               "account_weight": t.get("account_weight", t.get("base_weight", t["weight"])),
               "account_score": t.get("account_score_model",t.get("account_score")),
               "account_dimensions":t.get("account_dimensions",{}),
               "account_dimension_weights":t.get("account_dimension_weights",{}),
               "account_data_coverage":t.get("account_data_coverage",0),
               "account_confidence":t.get("account_confidence",0),
               "account_missing_dimensions":t.get("account_missing_dimensions",[]),
               "platform_score": t.get("platform_score", 0.5),
               "platform_dimensions":t.get("platform_dimensions",{}),
               "platform_data_coverage":t.get("platform_data_coverage",0),
               "platform_missing_dimensions":t.get("platform_missing_dimensions",[]),
               "golden_score": t.get("golden_score", 0),
               "golden_dimensions":t.get("golden_dimensions",{}),
               "novelty_score": t.get("novelty_score", 0),
               "final_score": t.get("final_score", t["weight"]),
               "score_breakdown": t.get("score_breakdown", {}),
               "score_formula_id": t.get("score_formula_id", analyze.UNIFIED_SCORE_FORMULA_ID),
               "avg_engagement": t.get("stats", {}).get("avg_engagement", 0),
               "platform_heat": t.get("stats", {}).get("platform_heat"),
               "recommendation_eligible":t.get("recommendation_eligible",False),
               "recommendation_status":t.get("recommendation_status","insufficient_evidence"),
               "evidence_confidence":t.get("evidence_confidence",0),
               "evidence_gate":t.get("evidence_gate",{}),
               "source": t.get("source", "原始")}
              for t in weights.get("topics", []) if t.get("recommendation_eligible")] if score_committed else []
    candidate_topics=[{"id":t.get("id"),"label":t.get("label"),
                       "internal":(t.get("evidence_gate") or {}).get("internal",False),
                       "external":(t.get("evidence_gate") or {}).get("external",False),
                       "status":t.get("recommendation_status","candidate_evidence_only")}
                      for t in weights.get("topics",[]) if not t.get("recommendation_eligible")]
    return {"account": account, "notes": notes, "topics": topics,"candidate_topics":candidate_topics,
            "account_intelligence":account_report,
            "scoring_formula": weights.get("scoring_formula", analyze.UNIFIED_SCORE_WEIGHTS),
            "scoring_formula_id": weights.get("scoring_formula_id", analyze.UNIFIED_SCORE_FORMULA_ID),
            "score_calculated_at": weights.get("score_calculated_at"),
            "score_committed":score_committed,"score_commit":weights.get("score_commit",{}),
            "weights_version": weights.get("version"), "updated_at": weights.get("updated_at")}


# ---------- 今日推荐(权重 + 实时热度融合)----------
def recommend(n: int = 5) -> list[dict]:
    """Topic Agent按唯一最终权重排序，不再维护第二套推荐分。"""
    return topic_agent.recommend(n)


def recommend_snapshot(n: int = 5) -> dict:
    """返回已提交推荐及其Pipeline身份；不在读取时计算新分数。"""
    weights=analyze.load_weights()
    commit=weights.get("score_commit") if isinstance(weights.get("score_commit"),dict) else {}
    committed=analyze.has_committed_score(weights)
    return {"topics":recommend(n) if committed else [],"score_committed":committed,
            "weights_version":weights.get("version"),"pipeline_id":commit.get("pipeline_id"),
            "committed_at":commit.get("committed_at")}


# ---------- 热门搜索(实时从平台抓取,同时用 sample.db 做热度标注)----------
def search_hot(keyword: str, limit: int = 15) -> dict:
    # 先取本地Golden证据。即使平台触发风控，已入库Golden仍可供写稿参考。
    golden_pack=golden_retriever.retrieve(keyword,k_samples=3,k_patterns=0,purpose="content")
    golden_results=[]
    for s in golden_pack.get("samples",[]):
        metrics=s.get("metrics",{}) if isinstance(s.get("metrics"),dict) else {}
        golden_likes=metrics.get("likes") or s.get("likes") or ""
        golden_results.append({
          "title":s.get("title", ""),"likes":str(golden_likes),
          "likes_num":sample_collect.parse_count(str(golden_likes)),"url":s.get("url", ""),
          "author":s.get("author", ""),"published_at":s.get("published_at", ""),
          "source":"golden_sample","is_golden":True,"golden_id":s.get("golden_id", ""),
          "content_type":s.get("content_type","unknown"),"analysis_scope":s.get("analysis_scope","metadata_only"),
          "body_excerpt":(s.get("body") or "")[:900],"retrieval_score":s.get("retrieval_score",0),
        })

    blocked=operation_policy.blocked_result("platform_search")
    if blocked:
        return {"ok":True,"results":golden_results,"platform_results":0,
                "golden_results":len(golden_results),
                "warning":"当前运行策略未允许平台搜索，只返回已有 Golden 证据。"}

    # 1) 实时从小红书平台搜索
    data, err = collect.run_cli(["search", keyword, "--limit", str(limit)])
    if err:
        msg = err.get("message", str(err))
        if golden_results:
            return {"ok":True,"results":golden_results,"platform_results":0,
                    "golden_results":len(golden_results),
                    "warning":"平台搜索暂不可用；已返回相关Golden Sample作为写稿证据: "+msg[:100]}
        if "401" in msg or "AUTH" in str(err.get("code", "")):
            return {"ok": False, "error": "需登录 www.xiaohongshu.com，请用 opencli 浏览器登录后重试", "results": []}
        if "RATE" in str(err.get("code", "")):
            return {"ok": False, "error": "请求太频繁，请稍后重试", "results": []}
        return {"ok": False, "error": msg[:120], "results": []}

    results = []
    for item in (data or []):
        likes_raw = str(item.get("likes", ""))
        results.append({
            "title": item.get("title", ""),
            "likes": likes_raw,
            "likes_num": sample_collect.parse_likes(likes_raw),
            "url": item.get("url", ""),
            "author": item.get("author", ""),
            "published_at": item.get("published_at", ""),
            "source":"platform_live","is_golden":False,
            "content_type":golden_repository.infer_content_type(item,"unknown"),
        })

    # 2) 如果有 sample.db, 用已有分析数据标注帖子的关键词/热度
    sdb = ROOT / "data" / "sample.db"
    if sdb.exists() and results:
        con = sqlite3.connect(sdb)
        con.row_factory = sqlite3.Row
        titles = [r["title"] for r in results]
        known = {
            row["title"]: {"keyword":row["keyword"],"body":row["body"]}
            for row in con.execute(
                "SELECT title, MAX(COALESCE(keyword,'')) keyword, "
                "MAX(COALESCE(body,'')) body FROM samples WHERE title IN ({}) GROUP BY title".format(
                    ",".join("?" * len(titles))), titles).fetchall()
        }
        con.close()
        for r in results:
            if r["title"] in known:
                r["keyword"] = known[r["title"]]["keyword"]
                r["body_excerpt"] = (known[r["title"]]["body"] or "")[:900]

    # Golden优先展示；按URL/title去重，避免同一篇同时出现在两种来源。
    combined=[]; seen=set()
    for item in [*golden_results,*results]:
        key=str(item.get("url") or item.get("title") or "").strip()
        if not key or key in seen: continue
        seen.add(key); combined.append(item)
    return {"ok": True, "results": combined,"platform_results":len(results),
            "golden_results":len(golden_results)}


# ---------- 数据分析:算权重(快)----------
def run_analyze() -> dict:
    """旧的单独重算入口已关闭，避免绕过样本与Golden前置阶段。"""
    weights=analyze.load_weights()
    return {"ok":False,"error":"正式Topic Score只能由‘外部采集→人工Golden确认→继续分析评分’完整流程更新",
            "score_committed":analyze.has_committed_score(weights),
            "version":weights.get("version"),"score_commit":weights.get("score_commit",{})}


# ---------- 采集(慢:opencli)----------
def collect_creator_data_center() -> dict:
    """只读官方账号数据中心并保存快照；不调用公开站搜索或逐篇适配器。"""
    blocked=operation_policy.blocked_result("creator_center_collect")
    if blocked: return blocked
    result = creator_data_center.collect_account_data()
    if not result.get("ok"):
        return result
    con = collect.init_db()
    try:
        saved = account_intelligence.save_data_center_snapshot(con, result)
        if result.get("observed_audience") or result.get("active_periods"):
            account_intelligence.save_audience_snapshot(con, {
                "observed": result.get("observed_audience") or {},
                "active_periods": [{"window": x} if isinstance(x, str) else x
                                   for x in (result.get("active_periods") or [])],
                "confidence": .95,
                "basis": "小红书创作者数据中心可见DOM",
            }, "creator_data_center_dom")
        today = date.today().isoformat()
        for key, value in (result.get("account_metrics") or {}).items():
            evidence = (result.get("metric_evidence") or {}).get(key, {})
            con.execute("INSERT OR REPLACE INTO account_stats VALUES (?,?,?,?)",
                        (today, key, value, "创作者数据中心:" + str(evidence.get("label") or key)))
        con.commit()
    finally:
        con.close()
    return {**result, "saved": saved, "metrics_count": len(result.get("account_metrics") or {}),
            "message": "创作者数据中心真实可见字段已保存；图表未暴露字段保持缺失"}


def run_collect() -> dict:
    """采集自己账号的总览指标 + 逐篇笔记指标，并写入 metrics.db。"""
    blocked=operation_policy.blocked_result("platform_collect")
    if blocked: return blocked
    today = date.today().isoformat()

    # 1) 官方创作者数据中心优先；仅当页面没有可用指标时才调用旧creator-stats。
    data_center = creator_data_center.collect_account_data()
    data_center_metrics = data_center.get("account_metrics", {}) if data_center.get("ok") else {}
    data_center_warning = ""
    if data_center_metrics:
        stats = []
        for key, value in data_center_metrics.items():
            evidence = (data_center.get("metric_evidence") or {}).get(key, {})
            stats.append({"metric": f"{evidence.get('label') or key} ({key})", "total": value,
                          "trend": "创作者数据中心"})
        err = None
        stats_source = "creator-data-center"
    else:
        raw_error = data_center.get("error") or {}
        data_center_warning = str(raw_error.get("message") or data_center.get("warning") or "数据中心页面未暴露可读指标")[:300]
        stats, err = collect.run_cli(["creator-stats", "--period", "thirty"])
        stats_source = "creator-stats"
        if err:
            msg = err.get("message", str(err))
            return {
                "ok": False,
                "error": "需登录 creator.xiaohongshu.com" if "401" in msg else msg,
                "data_center": data_center,
            }

    # 2) 逐篇笔记摘要 —— 这是账号内容表现的主数据源。
    summary, notes_err = collect.run_cli(["creator-notes-summary"])
    notes_warning = ""
    if notes_err:
        if notes_err.get("code") == "EMPTY_RESULT":
            summary = []
        elif data_center_metrics:
            summary = []
            notes_warning = str(notes_err.get("message") or notes_err)[:300]
        else:
            msg = notes_err.get("message", str(notes_err))
            return {
                "ok": False,
                "error": "逐篇数据采集失败: " + msg,
            }

    def extract_notes(payload):
        """兼容 OpenCLI 不同版本的 JSON 包装结构。"""
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("notes", "items", "list", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("notes", "items", "list", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def metric_int(value) -> int:
        """把 1234 / '1,234' / '1.2万' / '3.4k' 等转成整数。"""
        if value is None or value == "":
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip().lower().replace(",", "")
        multiplier = 1
        if text.endswith("万"):
            multiplier = 10000
            text = text[:-1]
        elif text.endswith("w"):
            multiplier = 10000
            text = text[:-1]
        elif text.endswith("k"):
            multiplier = 1000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

    # 数据中心逐篇表格优先提供扩展字段，creator-notes-summary补真实noteId；
    # 同标题合并，后者已有字段覆盖前者，避免一篇帖子重复入库。
    merged_notes = {}
    for item in [*(data_center.get("notes") or []), *extract_notes(summary)]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("title") or item.get("标题") or item.get("note_id") or item.get("id") or "").strip()
        if key:
            merged_notes[key] = {**merged_notes.get(key, {}), **item}
    notes = list(merged_notes.values())
    con = collect.init_db()
    attribution.bootstrap_local_drafts(con, DRAFTS)
    metrics_count = 0
    note_count = 0

    try:
        if data_center.get("ok"):
            account_intelligence.save_data_center_snapshot(con, data_center)
            if data_center.get("observed_audience") or data_center.get("active_periods"):
                account_intelligence.save_audience_snapshot(con, {
                    "observed": data_center.get("observed_audience") or {},
                    "active_periods": [{"window": x} if isinstance(x, str) else x
                                       for x in (data_center.get("active_periods") or [])],
                    "confidence": .95,
                    "basis": "小红书创作者数据中心可见DOM",
                }, "creator_data_center_dom")
        # 3) 先写 creator-stats 原始总览。
        for row in (stats or []):
            con.execute(
                "INSERT OR REPLACE INTO account_stats VALUES (?,?,?,?)",
                (
                    today,
                    collect.metric_key(row.get("metric", "")),
                    metric_int(row.get("total")),
                    str(row.get("trend", "")),
                ),
            )
            metrics_count += 1

        # 4) 写逐篇笔记数据，同时累计可可靠计算的账号核心指标。
        key_map = {
            "note_id": ["note_id", "id", "noteId"],
            "title": ["title", "标题"],
            "views": ["views", "view", "观看数", "观看", "曝光", "浏览量"],
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

        def pick(data, keys, default=0):
            for key in keys:
                if key in data:
                    return data[key]
            return default

        totals = {"notes": 0, "views": 0, "likes": 0, "favorites": 0, "comments": 0}

        mapped_notes = 0
        for note in notes:
            views = metric_int(pick(note, key_map["views"]))
            likes = metric_int(pick(note, key_map["likes"]))
            favorites = metric_int(pick(note, key_map["favorites"]))
            comments = metric_int(pick(note, key_map["comments"]))
            shares = metric_int(pick(note,key_map["shares"]))
            rise_fans = metric_int(pick(note,key_map["rise_fans"]))
            avg_time = collect.duration_seconds(pick(note,key_map["avg_view_time"],None))
            published_at = str(pick(note,key_map["published_at"],"") or "")
            top_source = str(pick(note,key_map["top_source"],"") or "")
            top_source_pct = collect.percent_value(pick(note,key_map["top_source_pct"],None))
            top_interest = str(pick(note,key_map["top_interest"],"") or "")
            top_interest_pct = collect.percent_value(pick(note,key_map["top_interest_pct"],None))
            impressions = collect.metric_number(pick(note,key_map["impressions"],None))
            clicks = collect.metric_number(pick(note,key_map["clicks"],None))
            ctr = collect.percent_value(pick(note,key_map["ctr"],None))
            completion_rate = collect.percent_value(pick(note,key_map["completion_rate"],None))
            home_views = metric_int(pick(note,key_map["home_views"]))
            note_id = str(pick(note, key_map["note_id"], ""))
            title = str(pick(note, key_map["title"], ""))
            topic_id = str(note.get("topic_id") or attribution.resolve_topic(con, note_id, title) or "")
            if topic_id:
                mapped_notes += 1
                attribution.mark_published(con, note_id, title, topic_id)

            values=(note_id,today,title,views,likes,favorites,comments,topic_id,published_at,shares,
                    avg_time,rise_fans,top_source,top_source_pct,top_interest,top_interest_pct,
                    impressions,clicks,ctr,completion_rate,home_views,json.dumps(note,ensure_ascii=False))
            con.execute("""INSERT OR REPLACE INTO note_metrics(
              note_id,collected,title,views,likes,favorites,comments,topic_id,published_at,shares,
              avg_view_time_seconds,rise_fans,top_source,top_source_pct,top_interest,top_interest_pct,
              impressions,clicks,ctr,completion_rate,home_views,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",values)
            snapshot=(note_id,datetime.now().isoformat(timespec="seconds"),title,topic_id,published_at,
                      views,likes,favorites,comments,shares,avg_time,rise_fans,top_source,top_source_pct,
                      top_interest,top_interest_pct,impressions,clicks,ctr,completion_rate,home_views,
                      json.dumps(note,ensure_ascii=False))
            con.execute("""INSERT OR REPLACE INTO note_metric_snapshots(
              note_id,collected_at,title,topic_id,published_at,views,likes,favorites,comments,shares,
              avg_view_time_seconds,rise_fans,top_source,top_source_pct,top_interest,top_interest_pct,
              impressions,clicks,ctr,completion_rate,home_views,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",snapshot)

            totals["notes"] += 1
            totals["views"] += views
            totals["likes"] += likes
            totals["favorites"] += favorites
            totals["comments"] += comments
            note_count += 1

        # 5) creator-stats 全 0 时，用逐篇汇总覆盖核心指标，避免看板显示假 0。
        stats_all_zero = bool(stats) and all(metric_int(r.get("total")) == 0 for r in stats)
        if note_count > 0 and (stats_all_zero or not stats):
            for metric in ("notes", "views", "likes", "favorites", "comments"):
                con.execute(
                    "INSERT OR REPLACE INTO account_stats VALUES (?,?,?,?)",
                    (today, metric, totals[metric], "逐篇汇总"),
                )

        con.commit()
    finally:
        con.close()

    try:
        golden_sync = golden_service.sync_performance_from_metrics(DB)
    except Exception as e:
        golden_sync = {"synced": 0, "errors": [str(e)[:160]]}
    return {
        "ok": True,
        "metrics": metrics_count,
        "notes": note_count,
        "mapped_notes": mapped_notes if note_count else 0,
        "stats_source": "notes-summary" if note_count > 0 and (not stats or stats_all_zero) else stats_source,
        "data_center": {"ok": bool(data_center.get("ok")), "url": creator_data_center.ACCOUNT_URL,
                        "source": data_center.get("source"), "metrics": list(data_center_metrics),
                        "page_count": data_center.get("page_count", 0),
                        "note_count": len(data_center.get("notes") or []),
                        "found_sections": data_center.get("found_sections", []),
                        "missing_sections": data_center.get("missing_sections", []),
                        "raw_path": data_center.get("raw_path", ""),
                        "warning": data_center_warning or data_center.get("warning", "")},
        "notes_warning": notes_warning,
        "golden_feedback_synced": golden_sync.get("synced", 0),
        "extended_metrics": {"shares":True,"rise_fans":True,"avg_view_time":True,
                             "traffic_source":True,"ctr":"available_only_when_platform_returns_it"},
    }


# ---------- 探测可用 LLM(给前端配置面板)----------
def probe_models() -> dict:
    return {"message": "可选择本机已登录 Codex CLI，或配置 Base URL + API Key + 模型名"}


# ---------- LLM 调用(统一走 OpenAI 兼容 /chat/completions)----------
def _parse_raw(raw: str) -> dict:
    """把 LLM 返回的纯文本按分隔符解析,失败降级 parse_freeform。复用 generate 的解析。"""
    title = generate._section(raw, "TITLE")
    body = generate._section(raw, "BODY")
    topics = [t.strip() for t in generate._section(raw, "TOPICS").replace("，", ",").split(",") if t.strip()]
    cover = generate._section(raw, "COVER")
    if title and body:
        return {"title": title, "body": body, "topics": topics, "cover_text": cover}
    return generate.parse_freeform(raw)


def call_llm(prompt: str, cfg: dict | None = None) -> dict:
    """统一走 OpenAI 兼容 /chat/completions 接口。
    cfg = {base_url, api_key, model}
    """
    cfg = cfg or {}
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    api_key = cfg.get("api_key") or ""
    model = cfg.get("model") or ""
    if not base:
        raise RuntimeError("未配置 Base URL，请在模型配置中填写")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps({"model": model,
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.loads(r.read().decode("utf-8"))
    raw = data["choices"][0]["message"]["content"].strip()
    return _parse_raw(raw)


# ---------- AI 写稿(慢)----------
def generate_draft(topic_id: str, refs: list[dict], model_config: dict | None = None,
                   topic_brief: dict | None = None) -> dict:
    """Content Agent: Topic + Golden Pattern + Golden 原文证据 -> 原创结构化稿件。"""
    persona = generate.load("persona.yaml")
    insurance = generate.load("insurance.yaml")
    weights = analyze.load_weights()
    topic = next((t for t in weights["topics"] if t["id"] == topic_id), None)
    if not topic:
        return {"ok": False, "error": f"未知选题 {topic_id}"}
    # 前端提交的是Topic Agent当次推荐生成的新Topic Brief；旧配置中的
    # title_patterns保留给历史兼容，但不会再进入Content Agent提示词。
    topic = {**topic, "topic_brief": topic_brief if isinstance(topic_brief, dict) else {}}
    try:
        content = content_agent.generate(topic, refs or [], model_config or {}, persona, insurance)
    except Exception as e:
        return {"ok": False, "error": "Content Agent 生成失败: " + str(e)[:300]}
    hits = generate.check_compliance(content, insurance)
    policy=compliance_policy.evaluate(content.get("title",""),content.get("body",""),content.get("cover_text",""),
                                      aigc_label_required=bool(persona.get("compliance",{}).get("aigc_label")))
    title = content.get("title", "")
    needs_title = _title_suspicious(title)
    if needs_title:
        content["title"] = f"【待填标题】{topic['label']}"
    return {"ok": True, "topic_id": topic_id, "category": topic["category"],
            "compliance_hits": hits, "compliance":policy,"publish_blocked":policy.get("status")=="BLOCK",
            "needs_title": needs_title, **content}


def _title_suspicious(t: str) -> bool:
    """标题是否不可信(需人工填):含大量英文、像话题串、太短、含代码符号。"""
    t = (t or "").strip()
    if len(t) < 5 or t in ("(待补标题)",):
        return True
    if any(c in t for c in ("`", "{", "}", "|")):          # 代码/格式碎片
        return True
    import re
    if len(re.findall(r"[a-zA-Z]", t)) > len(t) * 0.5:     # 过半英文字母
        return True
    parts = t.split()                                       # 多个短词空格连=话题串
    if len(parts) >= 4 and sum(len(p) <= 5 for p in parts) >= len(parts) - 1:
        return True
    return False


# ---------- 封面 V2: Cover Brief + 3候选 + HTML/CSS/Playwright ----------
def make_cover_img(cover_text: str, category: str, stem: str, title: str = "", topic_id: str = "", model_config: dict | None = None,
                   golden_usage_id: str="") -> dict:
    """Cover Agent 先给 brief，再交给现有 renderer 产 3 张候选。"""
    COVERS.mkdir(parents=True, exist_ok=True)
    topic_label = ""
    if topic_id:
        w = analyze.load_weights(); t = next((x for x in w.get("topics", []) if x.get("id") == topic_id), None)
        topic_label = (t or {}).get("label", "")
    plan = cover_agent.plan(title, cover_text, topic_label, model_config or {},golden_usage_id)
    planned_text = plan.get("headline") or cover_text or title or "(封面)"
    out = COVERS / f"{stem}.jpg"
    result = make_cover.make_cover_candidates(planned_text, category or "_default", out, title=title)
    candidates = result.get("candidates", [])
    if not candidates:
        return {"ok": False, "error": "封面生成没有产出候选图片"}
    return {"ok": True, "image_path": candidates[0]["path"], "images": candidates,
            "agent_plan": plan, "brief": result.get("brief", {}), "critic": result.get("critic", {}),
            "fallback": bool(result.get("fallback"))}


# ---------- 合规校验(快)----------
def check(title: str, body: str, cover_text: str="") -> dict:
    insurance = generate.load("insurance.yaml")
    hits = generate.check_compliance({"title": title, "body": body}, insurance)
    policy=compliance_policy.evaluate(title,body,cover_text,
             aigc_label_required=bool(generate.load("persona.yaml").get("compliance",{}).get("aigc_label")))
    quality=compliance_policy.quality_assessment(title,body,cover_text)
    return {"ok": len(hits) == 0 and policy.get("status")!="BLOCK", "hits": hits,
            "status":policy.get("status"),"policy":policy,"quality":quality}


# ---------- 推送草稿到小红书平台(opencli publish --draft)----------
def _extract_rows(payload) -> list[dict]:
    """兼容 OpenCLI list 类命令常见 JSON 包装结构。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("drafts", "items", "list", "results", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return _extract_rows(data)
    return []


def _draft_identity(row: dict) -> str:
    for key in ("id", "draft_id", "draftId", "uuid", "key"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _draft_title(row: dict) -> str:
    for key in ("title", "note_title", "noteTitle", "标题"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _draft_text_preview(row: dict) -> str:
    for key in ("textPreview", "text_preview", "preview", "content_preview", "desc", "description"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _draft_type(row: dict) -> str:
    for key in ("type", "note_type", "noteType"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _draft_images(row: dict) -> int:
    try:
        return int(row.get("images") or row.get("image_count") or row.get("imageCount") or 0)
    except (TypeError, ValueError):
        return 0


def _draft_title_saved(row: dict) -> bool:
    # OpenCLI drafts 会把真正的空标题规范化成 "(untitled)"。
    title = _draft_title(row).strip()
    return bool(title and title.lower() not in {"(untitled)", "untitled", "无标题"})


def _draft_content_signature(row: dict) -> tuple[str, int]:
    """只比较真正能代表草稿内容变化的字段，避免仅 updated_at 变化造成误判。"""
    return (_draft_text_preview(row).strip(), _draft_images(row))


def _draft_snapshot(retries: int = 1) -> tuple[list[dict], str]:
    """读取当前草稿箱。

    drafts 紧跟在 publish 后执行时，Browser Bridge 可能仍在创作者页自动保存；
    因此这里允许一次轻量重试，并把真实错误返回给上层用于诊断。
    """
    payload, err = collect.run_cli(["drafts"], retries=max(0, retries))
    if err:
        code = str(err.get("code") or "DRAFTS_ERROR")
        msg = str(err.get("message") or err)
        return [], f"{code}: {msg}"
    rows = _extract_rows(payload)
    if not rows:
        return [], "DRAFTS_EMPTY: drafts 返回成功但没有可解析的草稿行"
    return rows, ""


def _verify_draft_after_publish(
    title: str,
    before_rows: list[dict],
    initial_delay: float = 0.0,
) -> tuple[bool, str, dict | None, dict]:
    """验证 publish 后平台是否真的出现了草稿。

    优先级：
    1) 同标题精确匹配；
    2) 新 draft id（允许标题因 XHS/OpenCLI 问题为空）；
    3) publish timeout 后采用延迟 + 多次重查，避免页面尚未完成自动保存时过早判失败。
    """
    before_ids = {_draft_identity(r) for r in before_rows if _draft_identity(r)}
    before_map = {_draft_identity(r): r for r in before_rows if _draft_identity(r)}
    wanted = attribution.normalize_title(title)
    warnings = []
    last_after_rows: list[dict] = []

    if initial_delay > 0:
        time.sleep(initial_delay)

    # 自动保存存在延迟；越往后间隔稍长，但不重复触发 publish。
    delays = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            time.sleep(delay)

        after_rows, warning = _draft_snapshot(retries=1)
        if warning:
            warnings.append(f"第{attempt}次 drafts: {warning}")
            continue

        last_after_rows = after_rows

        # 最强验证：同标题。
        if wanted:
            matches = [r for r in after_rows
                       if attribution.normalize_title(_draft_title(r)) == wanted]
            if matches:
                row = matches[0]
                return True, _draft_identity(row), row, {
                    "matched_by": "title",
                    "title_saved": _draft_title_saved(row),
                    "body_saved": bool(_draft_text_preview(row).strip()),
                    "partial": False,
                    "attempts": attempt,
                    "before_count": len(before_rows),
                    "after_count": len(after_rows),
                    "draft_warnings": warnings,
                }

        # 标题可能为空；只要出现发布前不存在的新 id，就确认平台创建了新草稿。
        new_rows = [r for r in after_rows
                    if _draft_identity(r) and _draft_identity(r) not in before_ids]
        if new_rows:
            row = next((r for r in new_rows
                        if _draft_text_preview(r).strip() or _draft_type(r).lower() == "image"), new_rows[0])
            return True, _draft_identity(row), row, {
                "matched_by": "new_draft_id",
                "title_saved": _draft_title_saved(row),
                "body_saved": bool(_draft_text_preview(row).strip()),
                "partial": not _draft_title_saved(row),
                "attempts": attempt,
                "before_count": len(before_rows),
                "after_count": len(after_rows),
                "draft_warnings": warnings,
            }

        # 有些 XHS 页面会复用已有 draft id，在同一个 IndexedDB row 上继续自动保存。
        # 这种情况下没有“新 id”，但正文预览或图片数会发生真实变化。
        changed_rows = []
        for row in after_rows:
            draft_id = _draft_identity(row)
            old = before_map.get(draft_id)
            if not draft_id or old is None:
                continue
            old_preview, old_images = _draft_content_signature(old)
            new_preview, new_images = _draft_content_signature(row)
            content_changed = (new_preview and new_preview != old_preview) or (new_images > old_images)
            if content_changed:
                changed_rows.append(row)

        if changed_rows:
            row = next((r for r in changed_rows if _draft_text_preview(r).strip()), changed_rows[0])
            return True, _draft_identity(row), row, {
                "matched_by": "updated_existing_draft",
                "title_saved": _draft_title_saved(row),
                "body_saved": bool(_draft_text_preview(row).strip()),
                "partial": not _draft_title_saved(row),
                "attempts": attempt,
                "before_count": len(before_rows),
                "after_count": len(after_rows),
                "draft_warnings": warnings,
            }

    return False, "", None, {
        "matched_by": "none",
        "title_saved": False,
        "body_saved": False,
        "partial": False,
        "attempts": len(delays),
        "before_count": len(before_rows),
        "after_count": len(last_after_rows),
        "before_ids": sorted(before_ids)[:10],
        "after_ids": [_draft_identity(r) for r in last_after_rows if _draft_identity(r)][:10],
        "draft_warnings": warnings,
        "warning": (warnings[-1] if warnings else "发布后未发现新增草稿或同标题草稿"),
    }

def publish(title: str, body: str, topics: list[str], images: str,
            category: str, topic_id: str, draft: bool = True, golden_usage_id: str = "",
            cover_text: str="") -> dict:
    """保存本地草稿，并通过固定 `xhs-ui` Browser session 推送到小红书草稿箱。

    这条主链路不再调用 `opencli xiaohongshu publish`，因此也不再被旧
    adapter 的 180 秒等待/标题持久化问题阻塞。interactive=0 不是失败条件；
    xhs_ui_publish 使用 selector-first browser commands 定位控件。
    """
    blocked=operation_policy.blocked_result("platform_publish")
    if blocked: return blocked
    # Server-side release gate: direct API calls cannot bypass either check.
    insurance = generate.load("insurance.yaml")
    compliance_hits = generate.check_compliance({"title": title, "body": body}, insurance)
    if compliance_hits:
        return {"ok": False, "local_saved": False, "platform_saved": False,
                "error": "合规校验未通过: " + ",".join(compliance_hits),
                "compliance_hits": compliance_hits}
    policy=compliance_policy.evaluate(title,body,cover_text,
                                      aigc_label_required=bool(generate.load("persona.yaml").get("compliance",{}).get("aigc_label")))
    if policy.get("status")=="BLOCK":
        return {"ok":False,"local_saved":False,"platform_saved":False,
                "error":"发布合规门禁未通过: "+"；".join(x.get("message","") for x in policy.get("issues",[]) if x.get("severity")=="BLOCK"),
                "policy":policy}

    weights = analyze.load_weights()
    topic = next((t for t in weights.get("topics", []) if t.get("id") == topic_id), None)
    if not topic:
        return {"ok": False, "local_saved": False, "platform_saved": False,
                "error": "Reviewer 需要有效 topic_id，请先在今日选题中选择一个选题"}

    usage = golden_repository.get_usage(golden_usage_id) or {}
    review_content = {
        "title": title, "body": body, "cover_text": cover_text or (Path(images).stem if images else ""),
        "evidence": {
            "golden_ids": usage.get("golden_ids", []),
            "pattern_ids": usage.get("pattern_ids", []),
            "used_moves": (usage.get("used_features") or {}).get("used_moves", []),
        },
    }
    review = reviewer_agent.review(review_content, topic, insurance)
    if review.get("status") != "PASS":
        return {"ok": False, "local_saved": False, "platform_saved": False,
                "error": "Reviewer 未通过: " + "；".join(review.get("issues", [])),
                "review": review}

    DRAFTS.mkdir(parents=True, exist_ok=True)
    local_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    stem = f"{date.today().isoformat()}_{topic_id or 'manual'}_{local_id[-8:]}"
    draft_obj = {
        "local_id": local_id,
        "topic_id": topic_id,
        "category": category,
        "title": title,
        "body": body,
        "topics": topics,
        "images": images,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "golden_usage_id": golden_usage_id or "",
    }
    out = DRAFTS / f"{stem}.json"
    out.write_text(json.dumps(draft_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    if golden_usage_id:
        golden_repository.bind_usage_output(golden_usage_id, local_id)

    con = collect.init_db()
    try:
        attribution.register_local_draft(
            con, local_id=local_id, topic_id=topic_id, title=title,
            local_path=str(out), status="local_saved",
        )
    finally:
        con.close()

    base_result = {
        "draft": bool(draft), "path": str(out), "result": draft_obj,
        "local_id": local_id, "local_saved": True,
        "platform_saved": False, "platform_partial": False,
        "title_saved": False, "body_saved": False,
        "publish_mode": "xhs_ui",
        "review": review,
    }

    if not draft:
        return {**base_result, "ok": False,
                "error": "当前 xhs-ui 版本只负责推送到草稿箱，不执行全自动正式发布"}
    if not title.strip():
        return {**base_result, "ok": False, "error": "标题为空，未推送平台"}
    if len(title.strip()) > 20:
        return {**base_result, "ok": False,
                "error": f"标题共 {len(title.strip())} 个字符，小红书图文标题需 ≤20 字，请先缩短标题"}
    if not body.strip():
        return {**base_result, "ok": False, "error": "正文为空，未推送平台"}
    if not images:
        return {**base_result, "ok": False,
                "error": "本地草稿已保存，但无封面图，未推送到小红书草稿箱"}
    img_path = Path(images)
    if not img_path.exists():
        return {**base_result, "ok": False,
                "error": f"本地草稿已保存，但封面图不存在: {images}"}

    ui = push_to_xhs_draft(
        title=title.strip(),
        body=body,
        image_path=str(img_path.resolve()),
        topics=topics,
    )

    if not ui.get("ok"):
        con = collect.init_db()
        try:
            attribution.mark_failed(con, local_id)
        finally:
            con.close()
        return {
            **base_result,
            "ok": False,
            "platform": ui,
            "verification": {
                "checked": True,
                "matched": False,
                "matched_by": "xhs_ui",
                "steps": ui.get("steps", []),
            },
            "error": "本地草稿已保存；xhs-ui 推送失败: " + str(ui.get("error") or "未知错误")[:500],
        }

    ui_confirmed = bool(ui.get("ui_confirmed"))
    con = collect.init_db()
    try:
        attribution.mark_draft(
            con, local_id, "",
            "draft_confirmed_ui" if ui_confirmed else "draft_save_invoked_ui"
        )
    finally:
        con.close()

    result = {
        **base_result,
        "ok": True,
        "platform_saved": True,
        "platform_partial": not ui_confirmed,
        "title_saved": bool(ui.get("title_saved", True)),
        "body_saved": bool(ui.get("body_saved", True)),
        "platform": ui,
        "verification": {
            "checked": True,
            "matched": ui_confirmed,
            "matched_by": "xhs_ui_ui_marker" if ui_confirmed else "xhs_ui_save_invoked",
            "save_invoked": bool(ui.get("save_invoked")),
            "ui_confirmed": ui_confirmed,
            "ui": ui.get("verification", {}),
            "steps": ui.get("steps", []),
        },
    }
    if ui.get("warning"):
        result["warning"] = ui["warning"]
    return result

# ---------- 健身样本库 + 爆火因子 ----------
def samples_analysis() -> dict:
    sdb = ROOT / "data" / "sample.db"
    if not sdb.exists():
        return {"ok": False, "error": "样本库为空,请先跑选题科学化"}
    # 确保 Golden V1 新增的 note_id/body 等兼容列已经迁移。
    _tmp = sample_collect.init_db(); _tmp.close()
    con = sqlite3.connect(sdb)
    con.row_factory = sqlite3.Row
    data_date=con.execute("SELECT MAX(collected) FROM samples").fetchone()[0]
    top = [dict(r) for r in con.execute(
        "SELECT title, author, MAX(likes_raw) as likes_raw, MAX(likes_num) as likes_num, "
        "MAX(COALESCE(collects_raw,'')) as collects_raw, MAX(COALESCE(collects_num,0)) as collects_num, "
        "MAX(COALESCE(comments_raw,'')) as comments_raw, MAX(COALESCE(comments_num,0)) as comments_num, "
        "MAX(COALESCE(detail_fetched,0)) as detail_fetched, "
        "MAX(COALESCE(note_id,'')) as note_id, MAX(COALESCE(body,'')) as body, "
        "MAX(COALESCE(detail_json,'')) as detail_json, "
        "CASE WHEN SUM(CASE WHEN content_type='video' THEN 1 ELSE 0 END)>0 THEN 'video' "
        "WHEN SUM(CASE WHEN content_type='image_text' THEN 1 ELSE 0 END)>0 THEN 'image_text' "
        "ELSE 'unknown' END as content_type, "
        "MAX(COALESCE(content_type_source,'unknown')) as content_type_source, "
        "GROUP_CONCAT(DISTINCT keyword) as keyword, MAX(published_at) as published_at, url "
        "FROM samples WHERE collected=(SELECT MAX(collected) FROM samples) "
        "GROUP BY url ORDER BY likes_num DESC LIMIT 30").fetchall()]
    con.close()
    for item in top:
        stored=golden_repository.normalize_content_type(item.get("content_type"))
        item["content_type"]=stored if stored!="unknown" else golden_repository.infer_content_type(item,"unknown")
    media_counts={k:sum(x.get("content_type")==k for x in top) for k in ("image_text","video","unknown")}
    return {"ok": True, "top": top, "sample_n":len(top),"data_date":data_date,
            "media_counts":media_counts,"factors": heat.analyze_factors()}

# ---------- 人机协同选题科学化 ----------
def run_pipeline(discover_new: bool = True, model_config: dict | None = None,
                 phase: str = "continue_after_human") -> dict:
    blocked=operation_policy.blocked_result("platform_collect")
    if blocked: return blocked
    return pipeline.run(discover_new, model_config or {}, phase=phase)


def xhs_verification_status() -> dict:
    """检查由V6保留的前台小红书认证窗口。"""
    blocked=operation_policy.blocked_result("verification_session")
    if blocked: return blocked
    return sample_collect.verification_status()


# ---------- Golden Sample / Pattern ----------
def golden_overview() -> dict:
    return golden_service.overview()

def golden_discover(n: int = 10) -> dict:
    return golden_service.discover(n)

def golden_human_select(note_ref: str, reason: str = "人工判断值得学习", content_type: str = "unknown") -> dict:
    try:
        return golden_service.human_select(note_ref, reason, content_type)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

def golden_analyze(model_config: dict | None = None, limit: int = 20) -> dict:
    before=golden_service.overview()
    checkpoint=pipeline.human_checkpoint(before.get("candidates") or [])
    if not checkpoint.get("ok"):
        return {"ok":False,"error":checkpoint.get("error"),"human_gate":False}
    result=golden_service.analyze(model_config or {}, limit)
    after=golden_service.overview(); counts=after.get("counts") or {}
    hydrate=result.get("hydrate") or {}
    feature_errors=((result.get("feature_analysis") or {}).get("errors") or [])
    if hydrate.get("hard_stop") or (hydrate.get("errors") or []) or feature_errors or any(int(counts.get(k) or 0)<=0 for k in ("golden","patterns")):
        return {**result,"ok":False,
                "error":"本轮Golden Feature/Sample/Pattern未完整建立，不能进入C融合阶段",
                "counts":counts}
    pipeline.mark_golden_analyzed(checkpoint["stage"],counts)
    return {**result,"ok":True,"counts":counts,
            "human_current_batch":len(checkpoint.get("human_current") or []),
            "checkpoint":"golden_analyzed"}

def golden_rebuild_patterns() -> dict:
    return golden_service.rebuild()

def golden_revalidate(limit: int = 10) -> dict:
    return golden_service.revalidate(limit)


def operation_status() -> dict:
    return {"ok":True,"mode":operation_policy.MODE,
            "blocked_actions":sorted(operation_policy.blocked_actions())}


def daily_topic_decision(model_config: dict | None=None) -> dict:
    return personal_workflow.daily_decision(model_config or {})


def run_personal_daily(payload: dict | None=None) -> dict:
    return personal_workflow.run_daily(payload or {})

def golden_classify_media(candidate_id: str, content_type: str) -> dict:
    try:
        return golden_service.classify_media(candidate_id,content_type)
    except ValueError as e:
        return {"ok":False,"error":str(e)}

def golden_feedback(usage_id: str, views: int, likes: int, favorites: int, comments: int, **extra) -> dict:
    try:
        return golden_service.feedback(usage_id=usage_id, views=views, likes=likes, favorites=favorites, comments=comments,**extra)
    except ValueError as e:
        return {"ok": False, "error": str(e)}


def account_intelligence_overview() -> dict:
    con=collect.init_db()
    try:
        weights=analyze.load_weights()
        return {"ok":True,**account_intelligence.report(con,weights.get("topics",[]))}
    finally:
        con.close()


def save_account_audience(payload: dict) -> dict:
    con=collect.init_db()
    try:
        saved=account_intelligence.save_audience_snapshot(con,payload,
                    str(payload.get("source") or "creator_dashboard_import"))
        report=account_intelligence.report(con,analyze.load_weights().get("topics",[]))
        return {"ok":True,"audience":saved,"timing":report.get("timing",{})}
    finally:
        con.close()


# ---------- Agent Orchestrator / Reviewer ----------
def orchestrate(topic_id: str = "", refs: list[dict] | None = None, model_config: dict | None = None) -> dict:
    try:
        return orchestrator_agent.run(topic_id, refs or [], model_config or {})
    except Exception as e:
        return {"ok": False, "error": "Orchestrator 失败: " + str(e)[:400]}

def review_generated(topic_id: str, content: dict) -> dict:
    weights = analyze.load_weights(); topic = next((t for t in weights.get("topics", []) if t.get("id") == topic_id), None)
    if not topic: return {"ok": False, "error": f"未知选题 {topic_id}"}
    return {"ok": True, "review": reviewer_agent.review(content, topic, generate.load("insurance.yaml"))}
