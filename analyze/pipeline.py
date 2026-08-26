#!/usr/bin/env python3
"""带人工Golden检查点的选题科学化原子编排器。

流程:
  1. 平台Feed/搜索 → 健身热帖样本库 → 强制暂停
  2. 本轮人工确认Golden Candidate → Feature → Sample → Pattern
  3. Golden/平台/账号共同发现新选题
  4. 准备账号35% + 平台30% + Golden30% + 新颖度5%证据
  5. 所有门禁通过后，最后一次性原子提交正式Topic Score

用法: python analyze/pipeline.py [--no-discover]
"""
import importlib.util as _ilu
import json
import random
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE_STATE = ROOT / "data" / "topic_pipeline_stage.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load(path, name):
    spec = _ilu.spec_from_file_location(name, ROOT / path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload_rows(payload) -> list[dict]:
    """兼容Feed在不同OpenCLI版本中的 list/data/items/results/feeds 包装。"""
    value = payload
    for _ in range(4):
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if not isinstance(value, dict):
            return []
        next_value = None
        for key in ("data", "items", "results", "feeds", "feed", "notes"):
            candidate = value.get(key)
            if isinstance(candidate, (list, dict)):
                next_value = candidate
                break
        if next_value is None:
            return []
        value = next_value
    return []


def _write_stage_state(payload: dict) -> None:
    STAGE_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STAGE_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STAGE_STATE)


def _read_stage_state() -> dict:
    if not STAGE_STATE.exists():
        return {}
    try:
        return json.loads(STAGE_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _human_confirmations_after(candidates: list[dict], cutoff: str) -> list[dict]:
    """只承认本轮外部采集后的人工点击；历史Human或本轮Agent点击都不能冒充。"""
    confirmed=[]
    for candidate in candidates:
        selections=(candidate.get("source_reason") or {}).get("selections") or []
        human_now=any(str(item.get("type") or "") == "human"
                      and str(item.get("selected_at") or "") >= cutoff
                      for item in selections if isinstance(item,dict))
        if human_now:
            confirmed.append(candidate)
    return confirmed


def human_checkpoint(candidates: list[dict]) -> dict:
    """给Golden B3和最终C共同使用的本轮人工确认门禁。"""
    stage=_read_stage_state()
    if stage.get("status") not in {"awaiting_human_golden","golden_analyzed"}:
        return {"ok":False,"error":"没有等待人工选择的本轮外部采集批次；请先点击B1采集外部热帖"}
    selected=_human_confirmations_after(candidates,str(stage.get("collected_at") or ""))
    if not selected:
        return {"ok":False,"error":"本轮外部样本还没有人工Golden选择；请至少点1篇‘⭐设为人工Golden’",
                "stage":stage,"human_current":[]}
    return {"ok":True,"stage":stage,"human_current":selected}


def mark_golden_analyzed(stage: dict, counts: dict) -> None:
    _write_stage_state({**stage,"status":"golden_analyzed",
                        "golden_analyzed_at":datetime.now().isoformat(timespec="seconds"),
                        "golden_counts":counts})


def run(discover_new: bool = True, model_config: dict | None = None,
        phase: str = "continue_after_human") -> dict:
    collect_mod = _load("collect/collect.py", "_collect")
    sample_collect = _load("collect/sample_collect.py", "_sc")
    analyze = _load("analyze/analyze.py", "_analyze")
    heat_mod = _load("analyze/heat.py", "_heat")
    discover = _load("analyze/discover.py", "_discover")

    weights = analyze.load_weights()
    log = []
    pipeline_id = "topic_" + uuid.uuid4().hex[:12]
    cfg = model_config or {}
    if phase != "collect_external" and not all(str(cfg.get(k) or "").strip() for k in ("base_url", "api_key", "model")):
        return {"ok":False,"error":"完整流程需要先配置LLM Base URL、API Key和模型；正式Topic Score未改变",
                "log":["前置检查失败：Golden Feature与Discovery Agent没有可用模型配置"],
                "pipeline_id":pipeline_id,"score_committed":False}

    # 1) 先拉平台首页热门 Feed, 提取今日热搜关键词
    reuse_external = phase == "continue_after_human"
    log.append("使用第一阶段已采集的外部样本…" if reuse_external else "拉取平台热门 Feed…")
    try:
        feed_data, feed_err = (None, None) if reuse_external else collect_mod.run_cli(["feed", "--limit", "30"], retries=0)
    except RuntimeError as e:
        feed_data, feed_err = None, {"message": str(e)}
    feed_rows = _payload_rows(feed_data)
    if feed_err:
        feed_code = str(feed_err.get("code") or "ERROR").upper()
        msg = str(feed_err.get("message") or feed_err)[:180]
        log.append(f"Feed不可用 {feed_code}: {msg}")
        # Feed不是发现选题的唯一来源。普通格式/适配器故障允许降级到缓存和默认词；
        # 但认证、风控、限流时必须立刻停止，不能接着追加搜索与详情请求。
        if feed_code in {"AUTH", "SECURITY_CHECK", "SECURITY_BLOCK", "RATE_LIMITED"}:
            manual = False
            session = ""
            if feed_code in {"AUTH", "SECURITY_CHECK", "SECURITY_BLOCK"}:
                held = sample_collect.open_manual_verification_window(
                    f"Feed触发{feed_code}；前台窗口已保留，请人工完成认证"
                )
                manual = bool(held.get("manual_verification_required"))
                session = str(held.get("session") or "")
                log.append(str(held.get("message") or held.get("error") or "认证窗口已打开"))
            return {
                "ok": False,
                "error": "Feed已触发登录/安全限制，本轮未继续搜索或详情请求",
                "log": log,
                "hard_stop": {"code": feed_code, "message": msg},
                "manual_verification_required": manual,
                "verification_session": session,
                "pipeline_id": pipeline_id,
                "score_committed": False,
            }
        log.append("Feed已降级：继续使用样本库缓存/默认健身关键词，不阻断选题流程")
    if feed_rows:
        # 把 Feed 标题临时写入 sample 库, 供 extract_trending_keywords 使用
        from datetime import date as _dt
        scon = sample_collect.init_db()
        today = _dt.today().isoformat()
        # 健身相关关键词白名单，过滤掉与健身无关的 Feed 帖子
        _fitness_kw = ["健身", "运动", "减脂", "瘦", "练", "燃脂", "帕梅拉", "瑜伽", "跑步",
                       "拉伸", "体态", "腹肌", "马甲线", "增肌", "塑形", "HIIT", "有氧",
                       "卡路里", "热量", "饮食", "低卡", "食谱", "蛋白", "肌肉", "腿",
                       "臀", "手臂", "核心", "力量", "跳绳", "游泳", "骑行",
                       "爬坡", "器械", "新手", "跟练", "暴汗", "减肥", "轻断食",
                       "马拉松", "越野", "慢跑", "健走", "徒步", "登山", "户外",
                       "羽毛球", "篮球", "足球", "网球", "乒乓球", "攀岩",
                       "撸铁", "动感单车", "功能性训练", "运动康复", "康复", "普拉提", "铁三"]
        feed_matched = 0
        for i, item in enumerate(feed_rows):
            title = item.get("title") or item.get("note_title") or ""
            if title and any(kw in title for kw in _fitness_kw):
                likes = sample_collect.parse_likes(item.get("likes") or item.get("like_count") or "0")
                sample_collect._upsert_search_result(scon,today,"_feed_hot",{
                  **item,"rank":i,"title":title,"likes":item.get("likes") or item.get("like_count") or likes,
                  "url":item.get("url") or item.get("note_id") or "",
                })
                feed_matched += 1
        scon.commit()
        scon.close()
        log.append(f"Feed读取 {len(feed_rows)} 条 · 健身相关入库 {feed_matched} 条")
    elif not feed_err and not reuse_external:
        log.append("Feed返回0条：继续使用样本库缓存/默认健身关键词")

    # Feed 和搜索之间间隔 5-8s + jitter，避免被小红书反爬检测
    if feed_rows:
        time.sleep(5 + random.uniform(0, 3))

    # 从 Feed + 已有样本中提取今日热搜关键词
    db = ROOT / "data" / "sample.db"
    trending_kws = []
    if db.exists():
        scon = sqlite3.connect(db)
        try:
            trending_kws = heat_mod.extract_trending_keywords(scon, min_count=2, top_n=10)
        finally:
            scon.close()
    log.append(f"今日热搜关键词: {trending_kws or '(无样本,用默认词)'}")

    # 2) 用动态关键词 + 现有选题关键词一起搜索
    kws = sample_collect.diverse_keywords(trending_kws, slots=6)
    for t in weights["topics"]:
        k = t.get("heat_keyword") or t.get("label")
        if k and k not in kws:
            kws.append(k)
    # 小号低频模式：单轮最多 6 个关键词，避免一次科学化流程发出过多页面搜索。
    kws = kws[:6]
    log.append(f"采集 {len(kws)} 个关键词")
    # 基础采集不再无目标地提前抓Top 2详情；只给Human/Agent选中的Golden候选补详情。
    r = ({"total":0,"cached_keywords":len(kws),"failed":[],"fresh_signed_urls":0,
          "media_counts":{},"keyword_failures":[],"detail_failed":[]}
         if reuse_external else sample_collect.collect_samples(kws, limit=20, detail_top_n=0))
    if reuse_external:
        log.append("续跑阶段不重复请求平台，直接复用第一阶段样本")
    if not reuse_external:
        log.append(f"入库 {r['total']} 条 · 缓存关键词 {r.get('cached_keywords', 0)} · 关键词失败 {len(r['failed'])} · 新鲜signed URL {r.get('fresh_signed_urls', 0)}")
    mc=r.get("media_counts",{})
    log.append(f"媒介自动识别：图文 {mc.get('image_text',0)} · 视频 {mc.get('video',0)} · 待人工兜底 {mc.get('unknown',0)}")
    log.append("详情按需补全：请先Human/Agent选择Golden候选，再点‘补全详情并分析Feature’")
    for item in (r.get("keyword_failures") or [])[:3]:
        log.append(f"关键词失败 {item.get('keyword')} [{item.get('code')}]: {item.get('message')}")
    if r.get("dom_fallback_keywords"):
        log.append(f"搜索兼容兜底已启用: {len(r['dom_fallback_keywords'])} 个关键词通过 Browser DOM 入库")
    detail_failures=r.get("detail_failed",[]) or []
    for item in detail_failures[:3]:
        log.append(f"详情失败 {item.get('code') or 'ERROR'}: {item.get('message') or '未知错误'}")
    if r.get("detail_hard_stop"):
        stop = r.get("detail_hard_stop") or {}
        log.append(f"详情请求已停止 [{stop.get('code') or 'ERROR'}]，未继续请求第二篇")
    elif r.get("detail_skipped_reason"):
        log.append(f"详情未请求: {r.get('detail_skipped_reason')}")
    if r.get("hard_stop"):
        h = r["hard_stop"]
        log.append(f"平台采集已停止: {h.get('code')} {h.get('message')}")
        code = str(h.get("code") or "").upper()
        if code == "MANUAL_VERIFICATION_REQUIRED":
            error = "需要人工扫码认证；前台浏览器会话已保留，完成后请在看板检查认证"
        elif code.startswith("DOM_"):
            error = "OpenCLI 搜索失败，Browser DOM 兜底也未能读取页面；已停止后续请求"
        elif code == "AUTH":
            error = "小红书公共站登录态不可用；请在 Browser Bridge 对应 Profile 登录后重试"
        else:
            error = "平台限制/验证触发，本轮已停止后续请求"
        return {
            "ok": False,
            "error": error,
            "log": log,
            "hard_stop": h,
            "manual_verification_required": bool(h.get("manual_verification_required")),
            "verification_session": h.get("session", ""),
            "pipeline_id": pipeline_id,
            "score_committed": False,
        }

    # 没有健身热帖样本，就没有平台30%与Golden候选来源；禁止生成正式分数。
    scon=sqlite3.connect(ROOT / "data" / "sample.db")
    try:
        sample_rows=int(scon.execute(
            "SELECT COUNT(DISTINCT url) FROM samples WHERE collected=(SELECT MAX(collected) FROM samples)"
        ).fetchone()[0] or 0)
    finally:
        scon.close()
    if sample_rows <= 0:
        return {"ok":False,"error":"健身热帖样本库没有可用数据，Topic Score未改变",
                "log":log+["门禁失败：sample_rows=0"],"pipeline_id":pipeline_id,
                "score_committed":False}

    if phase == "collect_external":
        stage={"stage_id":"external_"+uuid.uuid4().hex[:12],
               "collected_at":datetime.now().isoformat(timespec="seconds"),
               "sample_rows":sample_rows,"status":"awaiting_human_golden"}
        _write_stage_state(stage)
        return {"ok":True,"phase":"awaiting_human_golden","awaiting_human_golden":True,
                "stage_id":stage["stage_id"],"sample_rows":sample_rows,"log":log,
                "message":"外部样本已采集。请在样本库人工选择Golden Candidate后再继续。",
                "pipeline_id":pipeline_id,"score_committed":False}

    # 3) 样本库 -> Agent/Human Candidate -> Feature -> Golden Sample -> Pattern。
    # Golden中心只更新证据，不得在这里单独写Topic Score。
    from golden import service as golden_service
    log.append("Golden中心：读取已经过B3人工确认与分析的本轮Golden证据…")
    stage=_read_stage_state()
    if stage.get("status") != "golden_analyzed":
        return {"ok":False,"error":"本轮Golden尚未完成B3分析；请先点击‘B3 确认本轮选择并建立Golden’",
                "log":log,"pipeline_id":pipeline_id,"score_committed":False}
    before=golden_service.overview()
    checkpoint=human_checkpoint(before.get("candidates") or [])
    if not checkpoint.get("ok"):
        return {"ok":False,"error":checkpoint.get("error"),"log":log,
                "stage_id":stage.get("stage_id"),"pipeline_id":pipeline_id,"score_committed":False}
    human_current=checkpoint["human_current"]
    golden_selection={"ok":True,"candidates":before.get("candidates") or [],
                      "human_current_batch":len(human_current)}
    golden_analysis={"hydrate":{},"feature_analysis":{},"admission":{}}
    golden_overview=before
    hydrate=golden_analysis.get("hydrate") or {}
    feature=golden_analysis.get("feature_analysis") or {}
    admission=golden_analysis.get("admission") or {}
    counts=golden_overview.get("counts") or {}
    log.append(f"Golden候选 {counts.get('candidates',0)} · Feature新增 {feature.get('analyzed',0)} · Golden Sample {counts.get('golden',0)} · Pattern {counts.get('patterns',0)}")
    log.append(f"本轮人工确认 {len(human_current)} 篇；Agent Candidate仅作为补充")
    log.append("外部路线完成：平台样本、PlatformHeat、Golden Sample/Pattern证据已准备")
    if hydrate.get("hard_stop"):
        return {"ok":False,"error":"Golden详情采集触发停止条件，完整流程未完成，Topic Score未改变",
                "log":log+["Golden详情停止: "+str((hydrate.get('hard_stop') or {}).get('error') or hydrate.get('hard_stop'))[:300]],
                "hard_stop":hydrate.get("hard_stop"),"pipeline_id":pipeline_id,"score_committed":False}
    if feature.get("errors"):
        return {"ok":False,"error":"Golden Feature分析存在失败，完整流程未完成，Topic Score未改变",
                "log":log+[f"Feature失败 {len(feature.get('errors') or [])} 篇"],
                "feature_errors":feature.get("errors"),"pipeline_id":pipeline_id,"score_committed":False}
    if any(int(counts.get(k) or 0)<=0 for k in ("candidates","golden","patterns")):
        return {"ok":False,"error":"Golden Candidate/Sample/Pattern证据不完整，Topic Score未改变",
                "log":log+["门禁失败：Candidate、Golden Sample、Pattern必须都大于0"],
                "pipeline_id":pipeline_id,"score_committed":False}

    # 4) 实时标题 × 已完成Golden Pattern共同发现新选题；先留在内存。
    discovery={"generated":0,"added":0,"merged":0,"error":""}
    if discover_new:
        try:
            topics = discover.discover_topics(5,model_config or {})
            m = discover.merge_into_weights(topics,weights=weights)
            discovery={"generated":len(topics),"added":len(m["added"]),"merged":int(m.get("merged",0)),"error":""}
            log.append(f"Discovery 候选 {discovery['generated']} · 新增 {discovery['added']} · 合并已有 {discovery['merged']} · 选题池 {m['total_topics']}")
            if m["added"]:
                log.append("实时Golden新选题: "+" / ".join(t.get("label","") for t in m["added"][:5]))
            weights = m["weights"]
        except Exception as e:
            discovery["error"]=str(e)[:300]
            log.append(f"Discovery 失败: {discovery['error']}")
            return {"ok":False,"error":"新选题Discovery未跑通，Topic Score未改变",
                    "log":log,"discovery":discovery,"pipeline_id":pipeline_id,
                    "score_committed":False}

    # 5) 账号证据准备。没有账号数据，账号35%没有来源，禁止提交正式分数。
    db = ROOT / "data" / "metrics.db"
    account_evidence=0
    if db.exists():
        con = sqlite3.connect(db)
        perf = analyze.topic_performance(con)
        weights = analyze.apply_account_intelligence(weights,con)
        try:
            account_evidence=int(con.execute(
                "SELECT COUNT(DISTINCT topic_id) FROM note_metrics WHERE COALESCE(topic_id,'')!='' AND views>0"
            ).fetchone()[0] or 0)
            account_evidence+=int(con.execute(
                "SELECT COUNT(*) FROM account_data_center_snapshots"
            ).fetchone()[0] or 0)
        except sqlite3.Error:
            pass
        con.close()
        if perf:
            weights = analyze.update_weights(weights, perf)
            covered=sum(1 for t in weights.get("topics",[]) if t.get("account_score_model") is not None)
            log.append(f"融合自己号完整账号分析({covered} 个选题；缺失指标不按0处理)")
    if account_evidence <= 0:
        return {"ok":False,"error":"账号35%没有可用账号证据，Topic Score未改变",
                "log":log+["门禁失败：请先采集创作者数据中心或完成帖子Topic归因"],
                "pipeline_id":pipeline_id,"score_committed":False}
    log.append("内部路线完成：账号概览、自帖、粉丝画像与AccountScore证据已准备")
    log.append("两路融合：内部机会＋外部机会生成选题，并汇总35/30/30/5 Topic Score")

    # 6) 所有前置阶段完成后，唯一一次原子提交35/30/30/5正式分数。
    evidence={"sample_rows":sample_rows,
              "golden_candidates":int(counts.get("candidates") or 0),
              "golden_features":int(counts.get("featured") or 0),
              "golden_samples":int(counts.get("golden") or 0),
              "golden_patterns":int(counts.get("patterns") or 0),
              "account_evidence":account_evidence,
              "discovery_generated":int(discovery.get("generated") or 0)}
    try:
        weights=analyze.commit_unified_scores(weights,pipeline_id,evidence)
    except Exception as e:
        return {"ok":False,"error":"最终Topic Score提交失败，旧版本保持不变",
                "log":log+[str(e)[:300]],"pipeline_id":pipeline_id,
                "score_committed":False}
    log.append(f"完整流程已通过；Topic Score原子提交至 v{weights['version']}（账号35% · 平台30% · Golden30% · 新颖度5%）")
    _write_stage_state({**stage,"status":"committed","committed_at":datetime.now().isoformat(timespec="seconds"),
                        "pipeline_id":pipeline_id,"weights_version":weights.get("version")})
    formal_count=int(weights.get("official_eligible_count") or 0)
    candidate_count=max(0,len(weights.get("topics",[]))-formal_count)
    log.append(f"证据准入：可评分推荐 {formal_count} 个 · 完全无真实证据候选 {candidate_count} 个（不进入柱状图）")

    ranked = sorted([t for t in weights["topics"] if t.get("recommendation_eligible")],
                    key=lambda t: t.get("final_score", t["weight"]), reverse=True)
    return {"ok":True,"log":log,"pipeline_id":pipeline_id,"score_committed":True,
            "weights_version":weights.get("version"),
            "scoring_formula":weights.get("scoring_formula",analyze.UNIFIED_SCORE_WEIGHTS),
            "scoring_formula_id":weights.get("scoring_formula_id",analyze.UNIFIED_SCORE_FORMULA_ID),
            "score_calculated_at":weights.get("score_calculated_at"),
            "evidence":evidence,
            "detail":{"enriched":hydrate.get("hydrated",0),"attempted":hydrate.get("attempted",0),
                      "selected":len(golden_selection.get("candidates") or []),
                      "failures":hydrate.get("errors",[]),"hard_stop":hydrate.get("hard_stop"),
                      "feature_analyzed":feature.get("analyzed",0),
                      "golden_admitted":admission.get("admitted",0)},
            "discovery":discovery,
            "ranking": [{"label": t["label"],
                         "account_weight": t.get("account_weight"),
                         "account_score": t.get("account_score"),
                         "platform_score": t.get("platform_score"),
                         "golden_score": t.get("golden_score"),
                         "novelty_score": t.get("novelty_score"),
                         "final_score": t.get("final_score", t["weight"]),
                         "weight": t.get("final_score", t["weight"]),
                         "heat": t.get("stats", {}).get("platform_heat"),
                         "source": t.get("source", "原始")} for t in ranked]}


def main():
    discover_new = "--no-discover" not in sys.argv
    r = run(discover_new)
    print("=== 选题科学化编排完成 ===")
    for line in r["log"]:
        print(" ", line)
    print("\n=== 选题权重排名 ===")
    for t in r.get("ranking", []):
        print(f"  {t['label']:<14} w={t['weight']:<6} 热度={t['heat']}  [{t['source']}]")


if __name__ == "__main__":
    main()
