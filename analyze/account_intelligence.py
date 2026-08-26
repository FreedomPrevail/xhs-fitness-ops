"""自己账号的数据分析层。

只使用创作者后台已经返回、或用户明确导入的聚合数据。缺失指标不按 0 分，
而是从可用维度重新归一化；推断与观测始终分开保存。
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "metrics.db"

NOTE_COLUMNS = {
    "published_at": "TEXT DEFAULT ''",
    "shares": "INTEGER DEFAULT 0",
    "avg_view_time_seconds": "REAL",
    "rise_fans": "INTEGER DEFAULT 0",
    "top_source": "TEXT DEFAULT ''",
    "top_source_pct": "REAL",
    "top_interest": "TEXT DEFAULT ''",
    "top_interest_pct": "REAL",
    "impressions": "INTEGER",
    "clicks": "INTEGER",
    "ctr": "REAL",
    "completion_rate": "REAL",
    "home_views": "INTEGER DEFAULT 0",
    "raw_json": "TEXT DEFAULT '{}'",
}

# AccountScore 只占最终 TopicScore 的 35%，这里定义其内部七维。
# 缺失维度不按 0 分，而是按可用权重重归一，并单独输出 coverage/confidence。
ACCOUNT_SCORE_WEIGHTS = {
    "historical_performance": .25,
    "interaction_structure": .15,
    "consumption_depth": .15,
    "follower_conversion": .10,
    "traffic_source": .10,
    "audience_fit": .15,
    "content_gap": .10,
}


def ensure_schema(con: sqlite3.Connection) -> None:
    """无损迁移 0813 旧 metrics.db。"""
    con.execute("""
        CREATE TABLE IF NOT EXISTS note_metrics (
          note_id TEXT, collected TEXT, title TEXT, views INTEGER DEFAULT 0,
          likes INTEGER DEFAULT 0, favorites INTEGER DEFAULT 0,
          comments INTEGER DEFAULT 0, topic_id TEXT DEFAULT '',
          PRIMARY KEY(note_id,collected)
        )
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(note_metrics)").fetchall()}
    for name, spec in NOTE_COLUMNS.items():
        if name not in cols:
            con.execute(f"ALTER TABLE note_metrics ADD COLUMN {name} {spec}")
    con.execute("""
        CREATE TABLE IF NOT EXISTS note_metric_snapshots (
          note_id TEXT, collected_at TEXT, title TEXT, topic_id TEXT,
          published_at TEXT DEFAULT '', views INTEGER DEFAULT 0,
          likes INTEGER DEFAULT 0, favorites INTEGER DEFAULT 0,
          comments INTEGER DEFAULT 0, shares INTEGER DEFAULT 0,
          avg_view_time_seconds REAL, rise_fans INTEGER DEFAULT 0,
          top_source TEXT DEFAULT '', top_source_pct REAL,
          top_interest TEXT DEFAULT '', top_interest_pct REAL,
          impressions INTEGER, clicks INTEGER, ctr REAL,
          completion_rate REAL, home_views INTEGER DEFAULT 0,
          raw_json TEXT DEFAULT '{}',
          PRIMARY KEY(note_id,collected_at)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS account_audience_snapshots (
          snapshot_at TEXT PRIMARY KEY, source TEXT NOT NULL,
          observed_json TEXT DEFAULT '{}', active_periods_json TEXT DEFAULT '[]',
          confidence REAL DEFAULT 0, basis TEXT DEFAULT ''
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS account_data_center_snapshots (
          snapshot_at TEXT PRIMARY KEY, source TEXT NOT NULL,
          metrics_json TEXT DEFAULT '{}', evidence_json TEXT DEFAULT '{}',
          raw_path TEXT DEFAULT '', url TEXT DEFAULT '',
          pages_json TEXT DEFAULT '[]', notes_json TEXT DEFAULT '[]'
        )
    """)
    dc_cols = {r[1] for r in con.execute("PRAGMA table_info(account_data_center_snapshots)").fetchall()}
    for name in ("pages_json", "notes_json"):
        if name not in dc_cols:
            con.execute(f"ALTER TABLE account_data_center_snapshots ADD COLUMN {name} TEXT DEFAULT '[]'")
    con.commit()


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(num, den):
    n, d = _num(num), _num(den)
    if n is None or d is None or d <= 0:
        return None
    return max(0.0, n / d)


def _mean(values):
    clean = [float(v) for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _weighted_available(values: list[tuple[float | None,float]]) -> float | None:
    clean=[(float(v),float(w)) for v,w in values if v is not None]
    denom=sum(w for _,w in clean)
    return sum(v*w for v,w in clean)/denom if denom else None


def _clamp(v):
    return max(0.0, min(1.0, float(v)))


def _normalize_metric(values: dict[str, float | None]) -> dict[str, float | None]:
    clean = [float(v) for v in values.values() if v is not None]
    if not clean:
        return {k: None for k in values}
    lo, hi = min(clean), max(clean)
    if hi <= lo:
        return {k: (0.5 if v is not None else None) for k, v in values.items()}
    return {k: (round((float(v) - lo) / (hi - lo), 4) if v is not None else None)
            for k, v in values.items()}


def _published_age_days(value: str, fallback: str = "") -> int | None:
    """发布天数校正；平台没给发布时间时退回采集时间，但不伪造日期。"""
    for text in (value, fallback):
        try:
            dt = datetime.fromisoformat(str(text or "").strip()[:19])
            return max(1, (datetime.now() - dt).days + 1)
        except (TypeError, ValueError):
            continue
    return None


def _audience_text(observed: dict) -> str:
    try:
        return json.dumps(observed or {}, ensure_ascii=False)
    except Exception:
        return ""


def _latest_rows(con: sqlite3.Connection) -> list[dict]:
    ensure_schema(con)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
      WITH ranked AS (
        SELECT *, ROW_NUMBER() OVER (
          PARTITION BY CASE WHEN COALESCE(note_id,'')!='' THEN note_id ELSE title END
          ORDER BY collected DESC
        ) rn
        FROM note_metrics
      ) SELECT * FROM ranked WHERE rn=1 AND COALESCE(topic_id,'')!=''
    """).fetchall()
    return [dict(r) for r in rows]


def latest_audience(con: sqlite3.Connection) -> dict:
    ensure_schema(con)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM account_audience_snapshots ORDER BY snapshot_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"source": "missing", "observed": {}, "active_periods": [],
                "confidence": 0.0, "basis": "创作者后台受众数据尚未采集或导入"}
    d = dict(row)
    try: d["observed"] = json.loads(d.pop("observed_json") or "{}")
    except Exception: d["observed"] = {}
    try: d["active_periods"] = json.loads(d.pop("active_periods_json") or "[]")
    except Exception: d["active_periods"] = []
    return d


def save_audience_snapshot(con: sqlite3.Connection, payload: dict,
                           source: str = "creator_dashboard_import") -> dict:
    """保存创作者后台聚合受众；不接受逐个点赞用户画像。"""
    ensure_schema(con)
    observed = payload.get("observed") if isinstance(payload.get("observed"), dict) else {}
    periods = payload.get("active_periods") if isinstance(payload.get("active_periods"), list) else []
    confidence = _clamp(payload.get("confidence", 1.0 if observed else 0.0))
    basis = str(payload.get("basis") or "创作者后台聚合数据")[:500]
    ts = str(payload.get("snapshot_at") or datetime.now().isoformat(timespec="seconds"))
    con.execute("INSERT OR REPLACE INTO account_audience_snapshots VALUES(?,?,?,?,?,?)",
                (ts, str(source)[:80], json.dumps(observed, ensure_ascii=False),
                 json.dumps(periods, ensure_ascii=False), confidence, basis))
    con.commit()
    return latest_audience(con)


def save_data_center_snapshot(con: sqlite3.Connection, payload: dict) -> dict:
    """Persist one official Creator Data Center read without flattening missing values to zero."""
    ensure_schema(con)
    ts = str(payload.get("snapshot_at") or datetime.now().isoformat(timespec="seconds"))
    metrics = payload.get("account_metrics") if isinstance(payload.get("account_metrics"), dict) else {}
    evidence = payload.get("metric_evidence") if isinstance(payload.get("metric_evidence"), dict) else {}
    pages = payload.get("pages") if isinstance(payload.get("pages"), list) else []
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    con.execute("""INSERT OR REPLACE INTO account_data_center_snapshots(
                  snapshot_at,source,metrics_json,evidence_json,raw_path,url,pages_json,notes_json)
                  VALUES(?,?,?,?,?,?,?,?)""",
                (ts, str(payload.get("source") or "creator_data_center_dom")[:80],
                 json.dumps(metrics, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False),
                 str(payload.get("raw_path") or "")[:500], str(payload.get("url") or "")[:500],
                 json.dumps(pages, ensure_ascii=False), json.dumps(notes, ensure_ascii=False)))
    con.commit()
    return {"snapshot_at": ts, "source": str(payload.get("source") or "creator_data_center_dom"),
            "account_metrics": metrics, "metric_evidence": evidence,
            "pages": pages, "notes": notes,
            "raw_path": str(payload.get("raw_path") or ""), "url": str(payload.get("url") or "")}


def latest_data_center_snapshot(con: sqlite3.Connection) -> dict:
    ensure_schema(con)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM account_data_center_snapshots ORDER BY snapshot_at DESC LIMIT 1").fetchone()
    if not row:
        return {"source": "missing", "account_metrics": {}, "metric_evidence": {}}
    value = dict(row)
    try: value["account_metrics"] = json.loads(value.pop("metrics_json") or "{}")
    except Exception: value["account_metrics"] = {}
    try: value["metric_evidence"] = json.loads(value.pop("evidence_json") or "{}")
    except Exception: value["metric_evidence"] = {}
    try: value["pages"] = json.loads(value.pop("pages_json") or "[]")
    except Exception: value["pages"] = []
    try: value["notes"] = json.loads(value.pop("notes_json") or "[]")
    except Exception: value["notes"] = []
    value["page_count"] = len(value.get("pages") or [])
    value["note_count"] = len(value.get("notes") or [])
    value["found_sections"] = list(dict.fromkeys(
        str(x.get("section")) for x in (value.get("pages") or [])
        if isinstance(x, dict) and x.get("section")))
    value["missing_sections"] = [x for x in ("账号概览", "内容分析", "粉丝数据")
                                 if x not in value["found_sections"]]
    return value


def _audience_similarity(label: str, interest: str) -> float | None:
    if not interest:
        return None
    def tokens(text: str) -> set[str]:
        out={x.lower() for x in re.findall(r"[A-Za-z0-9]+",text or "") if len(x)>=2}
        for seq in re.findall(r"[\u4e00-\u9fff]+",text or ""):
            for width in (2,3,4):
                out.update(seq[i:i+width] for i in range(max(0,len(seq)-width+1)))
        return out
    a = tokens(label or "")
    b = tokens(interest or "")
    if not a or not b:
        return None
    return min(1.0, len(a & b) / max(1, min(len(a), len(b))))


def topic_profiles(con: sqlite3.Connection, topic_labels: dict[str, str] | None = None) -> dict[str, dict]:
    """按选题计算七维 AccountScore，并保留缺失、覆盖率和置信度。"""
    topic_labels = topic_labels or {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in _latest_rows(con):
        grouped[str(row.get("topic_id"))].append(row)

    # 新发现选题即使还没有自帖，也要进入内容缺口/受众匹配分析；不能回退到旧权重。
    all_topic_ids = list(dict.fromkeys([*topic_labels.keys(), *grouped.keys()]))
    max_posts = max([len(v) for v in grouped.values()] or [0])
    account_audience = latest_audience(con)
    audience_blob = _audience_text(account_audience.get("observed") or {})

    raw: dict[str, dict] = {}
    for tid in all_topic_ids:
        rows = grouped.get(tid, [])
        history, interaction, consumption, conversion, distribution, audience = [], [], [], [], [], []
        for r in rows:
            try: payload_raw=json.loads(r.get("raw_json") or "{}")
            except Exception: payload_raw={}
            def observed(*keys):
                return any(k in payload_raw and payload_raw.get(k) not in (None,"") for k in keys)
            views = _num(r.get("views")) or 0
            like_rate = _ratio(r.get("likes"), views)
            fav_rate = _ratio(r.get("favorites"), views)
            comment_rate = _ratio(r.get("comments"), views)
            share_rate = _ratio(r.get("shares"), views) if observed("shares","share","分享数","分享") or float(r.get("shares") or 0)>0 else None
            if any(v is not None for v in (like_rate, fav_rate, comment_rate)):
                weighted_engagement = ((like_rate or 0) + 2 * (fav_rate or 0)
                                       + 3 * (comment_rate or 0) + 2 * (share_rate or 0))
                age = _published_age_days(str(r.get("published_at") or ""), str(r.get("collected") or ""))
                # 历史表现同时看账号内浏览规模、互动效率和发布天数，不直接比较绝对浏览。
                velocity = math.log1p(views / math.sqrt(age)) if age and views > 0 else None
                history.append(_mean([velocity, weighted_engagement * 10]))
                interaction.append(_weighted_available([
                    (like_rate,.25),(fav_rate,.35),(comment_rate,.25),(share_rate,.15)
                ]))
            avg_time = _num(r.get("avg_view_time_seconds"))
            completion = _num(r.get("completion_rate"))
            if completion is not None and completion > 1:
                completion /= 100
            consumption.append(_mean([avg_time, completion]))
            fan_rate = _ratio(r.get("rise_fans"), views) if observed("rise_fans","new_followers","followers_gained","涨粉数","涨粉") or float(r.get("rise_fans") or 0)>0 else None
            conversion.append(fan_rate)
            ctr = _num(r.get("ctr"))
            if ctr is None:
                ctr = _ratio(r.get("clicks"), r.get("impressions"))
            source_pct = _num(r.get("top_source_pct"))
            if source_pct is not None and source_pct > 1:
                source_pct /= 100
            distribution.append(_mean([ctr, source_pct / 100 if source_pct and source_pct > 1 else source_pct]))
            audience.append(_audience_similarity(topic_labels.get(tid, ""), str(r.get("top_interest") or "")))
        aggregate_audience = _audience_similarity(topic_labels.get(tid, ""), audience_blob)
        observed_audience = _mean([_mean(audience), aggregate_audience])
        raw[tid] = {
            "historical_performance": _mean(history), "interaction_structure": _mean(interaction),
            "consumption_depth": _mean(consumption),
            "follower_conversion": _mean(conversion), "traffic_source": _mean(distribution),
            "audience_fit": observed_audience,
            "content_gap": (1.0 - len(rows) / max_posts) if max_posts > 0 else None,
            "posts": len(rows),
        }

    dimension_keys = tuple(ACCOUNT_SCORE_WEIGHTS)
    normalized = {key: _normalize_metric({tid: d.get(key) for tid, d in raw.items()})
                  for key in dimension_keys}
    out = {}
    for tid, row in raw.items():
        dims = {k: normalized[k].get(tid) for k in dimension_keys}
        available = {k: v for k, v in dims.items() if v is not None}
        available_weight = sum(ACCOUNT_SCORE_WEIGHTS[k] for k in available)
        raw_score = (sum(ACCOUNT_SCORE_WEIGHTS[k] * v for k, v in available.items())
                     / available_weight) if available_weight else None
        # 数据覆盖率与自帖数量共同决定置信度；缺失仍不计0，但会向中性0.5收缩。
        coverage = available_weight
        n = int(row.get("posts") or 0)
        sample_confidence = n / (n + 3) if n > 0 else 0.0
        confidence = coverage * (.35 + .65 * sample_confidence) if raw_score is not None else 0.0
        score = (confidence * raw_score + (1 - confidence) * .5) if raw_score is not None else None
        out[tid] = {
            "score": round(score, 4) if score is not None else None,
            "dimensions": {k: (round(v, 4) if v is not None else None) for k, v in dims.items()},
            "raw_dimensions": {k: row.get(k) for k in dimension_keys},
            "dimension_weights": dict(ACCOUNT_SCORE_WEIGHTS),
            "coverage": round(coverage, 3), "confidence": round(confidence, 3),
            "available_dimensions": list(available), "missing_dimensions": [k for k in dimension_keys if k not in available],
            "posts": row.get("posts", 0),
            "evidence_observed": bool(n > 0 or aggregate_audience is not None),
        }
    return out


def timing_analysis(con: sqlite3.Connection) -> dict:
    """真实后台活跃时段优先；否则用历史发布时间×表现做低置信度估计。"""
    audience = latest_audience(con)
    if audience.get("active_periods"):
        return {"periods": audience["active_periods"], "source": audience.get("source"),
                "confidence": audience.get("confidence", 0), "basis": audience.get("basis", "")}
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in _latest_rows(con):
        text = str(r.get("published_at") or "")
        m = re.search(r"(?:T|\s)(\d{1,2}):", text)
        if not m or not _num(r.get("views")):
            continue
        hour = int(m.group(1))
        label = "06:00-10:59" if 6 <= hour < 11 else "11:00-13:59" if hour < 14 else \
                "14:00-17:59" if hour < 18 else "18:00-21:59" if hour < 22 else "22:00-05:59"
        rate = ((_num(r.get("likes")) or 0) + 2 * (_num(r.get("favorites")) or 0)
                + 3 * (_num(r.get("comments")) or 0)) / max(1, _num(r.get("views")) or 1)
        buckets[label].append(rate)
    ranked = sorted(((k, sum(v) / len(v), len(v)) for k, v in buckets.items()),
                    key=lambda x: x[1], reverse=True)
    periods = [{"window": k, "score": round(v, 4), "posts": n} for k, v, n in ranked[:3]]
    total = sum(x[2] for x in ranked)
    return {"periods": periods, "source": "historical_publish_performance" if periods else "missing",
            "confidence": round(min(.65, total / 20), 2) if periods else 0.0,
            "basis": "按自己帖子发布时间与最新互动表现估计；不是平台受众在线统计" if periods else "暂无小时级或后台活跃时段数据"}


def operations_analysis(con: sqlite3.Connection) -> dict:
    rows=_latest_rows(con)
    data_center=latest_data_center_snapshot(con)
    official=data_center.get("account_metrics") or {}
    if not rows:
        return {"posts":0,"status":"observed_account_only" if official else "missing",
                "official_account_metrics":official,
                "data_center_sections":data_center.get("found_sections",[]),
                "recommendations":["账号总览已进入运营分析；仍需内容分析逐篇表格完成具体Topic归因"] if official
                                  else ["先采集并完成已发布笔记的topic归因"]}
    views=sum(float(r.get("views") or 0) for r in rows)
    totals={k:sum(float(r.get(k) or 0) for r in rows) for k in ("likes","favorites","comments","shares","rise_fans")}
    dates=[]; topics=defaultdict(int)
    for r in rows:
        topics[str(r.get("topic_id") or "未归因")]+=1
        text=str(r.get("published_at") or "")[:10]
        try: dates.append(datetime.strptime(text,"%Y-%m-%d").date())
        except Exception: pass
    dates=sorted(set(dates)); intervals=[(b-a).days for a,b in zip(dates,dates[1:])]
    max_share=max(topics.values())/len(rows) if topics else 0
    recommendations=[]
    if len(rows)<8: recommendations.append("当前可归因帖子较少，权重已做小样本平滑；继续积累真实反馈")
    if max_share>.6: recommendations.append("选题集中度较高，保留主线同时增加相邻受众痛点的探索")
    if totals["favorites"]>totals["likes"]*.5: recommendations.append("收藏结构较强，优先延续清单、步骤和可执行内容")
    if totals["rise_fans"]<=0: recommendations.append("涨粉字段尚无有效值，暂不能判断账号定位转化")
    if official.get("ctr") is not None and float(official.get("ctr") or 0)<.03:
        recommendations.append("数据中心账号CTR偏低，优先检查选题、标题与封面进入率")
    if official.get("avg_view_time_seconds") is not None and float(official.get("avg_view_time_seconds") or 0)<5:
        recommendations.append("数据中心平均观看时间偏短，优先改善开头承接和内容节奏")
    return {"posts":len(rows),"views":int(views),
            "rates":{k:round(v/max(1,views),4) for k,v in totals.items()},
            "topic_mix":dict(sorted(topics.items(),key=lambda x:x[1],reverse=True)),
            "topic_concentration":round(max_share,3),
            "average_publish_interval_days":round(sum(intervals)/len(intervals),2) if intervals else None,
            "official_account_metrics":official,
            "data_center_sections":data_center.get("found_sections",[]),
            "status":"observed_partial","recommendations":recommendations}


def report(con: sqlite3.Connection, topics: list[dict] | None = None) -> dict:
    topics = topics or []
    labels = {str(t.get("id")): str(t.get("label") or "") for t in topics}
    profiles = topic_profiles(con, labels)
    ranked = sorted(({"topic_id": tid, "label": labels.get(tid, tid), **p}
                     for tid, p in profiles.items()),
                    key=lambda x: x.get("score") if x.get("score") is not None else -1, reverse=True)
    audience = latest_audience(con)
    data_center = latest_data_center_snapshot(con)
    timing = timing_analysis(con)
    opportunities = []
    for item in ranked[:5]:
        dims = item.get("dimensions", {})
        strength = max((k for k, v in dims.items() if v is not None),
                       key=lambda k: dims[k], default="historical_performance")
        opportunities.append({"topic_id": item["topic_id"], "label": item["label"],
                              "reason": f"账号{strength}相对较强", "account_score": item.get("score"),
                              "audience_evidence": audience.get("observed", {}),
                              "recommended_periods": timing.get("periods", [])[:2]})
    return {"topics": ranked, "audience": audience, "timing": timing,
            "data_center": data_center,
            "operations":operations_analysis(con),
            "opportunities": opportunities,
            "limitations": [
                "没有创作者后台逐篇受众时，不能断言具体点赞者画像",
                "CTR只有在获得曝光/点击或平台CTR字段时才参与",
                "历史发布时间相关性不等于受众真实在线高峰",
            ]}
