#!/usr/bin/env python3
"""L3 数据分析 —— 闭环的大脑。

读 SQLite 历史指标,按选题(topic_id)聚合出平均互动率,
用 multi-armed bandit 思路更新 data/topic_weights.json 的 weight:
  - 表现好的选题 weight 调高(exploit)
  - 保留 epsilon 探索率给新选题(explore)
generate.py 下一轮就会更偏向高权重选题 —— 这就是"数据改变方法和权重"。
"""
import copy
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "metrics.db"
WEIGHTS = ROOT / "data" / "topic_weights.json"
WEIGHTS_SEED = ROOT / "config" / "topic_weights.example.json"

# 权重更新参数
SMOOTHING = 0.5        # 新旧权重融合系数(0=不更新,1=完全用新值)
MIN_WEIGHT = 0.2       # 权重下限,保证每类选题仍有被抽中的机会
MAX_WEIGHT = 3.0       # 权重上限,防止单一选题垄断

# 唯一选题权重公式。所有看板、推荐、抽样均读取同一个 final_score/weight。
UNIFIED_SCORE_WEIGHTS = {
    "account": 0.35,
    "platform": 0.30,
    "golden": 0.30,
    "novelty": 0.05,
}
UNIFIED_SCORE_FORMULA_ID = "account35_platform30_golden30_novelty5_v2"
ACCOUNT_SCORE_FORMULA_ID = "history25_interaction15_consumption15_follow10_source10_audience15_gap10_v1"
PLATFORM_SCORE_FORMULA_ID = "engagement40_momentum25_highheat20_coverage15_v1"


def _validate_unified_formula() -> None:
    """Fail closed if anybody accidentally changes the canonical topic formula."""
    expected = {"account": 0.35, "platform": 0.30, "golden": 0.30, "novelty": 0.05}
    if UNIFIED_SCORE_WEIGHTS != expected or abs(sum(UNIFIED_SCORE_WEIGHTS.values()) - 1.0) > 1e-12:
        raise RuntimeError("选题权重公式必须固定为账号35%+平台30%+Golden30%+新颖度5%")


def load_weights() -> dict:
    """读取权重配置，并兼容旧版本字段。

    account/platform/golden/novelty只是同一公式的可解释因子；
    final_score才是唯一选题权重，weight始终镜像它。

    weight 保留为兼容字段，始终镜像 final_score，避免旧的生成逻辑失效。
    """
    if not WEIGHTS.exists():
        if not WEIGHTS_SEED.exists():
            raise FileNotFoundError(f"找不到 Topic 权重种子：{WEIGHTS_SEED}")
        data = json.loads(WEIGHTS_SEED.read_text(encoding="utf-8"))
        WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
        tmp = WEIGHTS.with_name(WEIGHTS.name + ".init-tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(WEIGHTS)
    else:
        data = json.loads(WEIGHTS.read_text(encoding="utf-8"))
    for t in data.get("topics", []):
        base = float(t.get("base_weight", t.get("weight", 1.0)))
        t.setdefault("base_weight", base)
        # 旧版本的 weight 已混入平台热度，因此首次迁移从 base_weight 开始，避免污染账号分。
        t.setdefault("account_weight", base)
        t.setdefault("account_score", 1.0)
        t.setdefault("platform_score", 0.5)
        t.setdefault("golden_score", 0.0)
        t.setdefault("novelty_score", 0.7 if t.get("source") == "discovered" else 0.2)
        t.setdefault("final_score", float(t.get("weight", base)))
        t["weight"] = float(t.get("final_score", t["account_weight"]))
    data["scoring_formula"] = dict(UNIFIED_SCORE_WEIGHTS)
    return data


def has_committed_score(weights: dict) -> bool:
    """Only a successfully completed scientific pipeline creates an official score."""
    commit = weights.get("score_commit") if isinstance(weights.get("score_commit"), dict) else {}
    return (commit.get("status") == "committed"
            and commit.get("formula_id") == UNIFIED_SCORE_FORMULA_ID
            and bool(commit.get("pipeline_id")))


def commit_unified_scores(weights: dict, pipeline_id: str, evidence: dict) -> dict:
    """Calculate and atomically publish the one official Topic Score snapshot.

    Callers must finish sample collection, Golden analysis and account/platform
    evidence preparation before invoking this function.  No other path writes
    final_score/weight to the official configuration.
    """
    required = ("sample_rows", "golden_candidates", "golden_samples", "golden_patterns",
                "account_evidence")
    missing = [key for key in required if int(evidence.get(key) or 0) <= 0]
    if missing:
        raise RuntimeError("Topic Score前置证据不足: " + ", ".join(missing))
    if not str(pipeline_id or "").strip():
        raise RuntimeError("Topic Score提交缺少pipeline_id")

    committed = apply_unified_scores(copy.deepcopy(weights), bump_version=True)
    if int(committed.get("official_eligible_count") or 0) <= 0:
        raise RuntimeError("没有同时具备内部账号证据和外部平台/Golden证据的选题，拒绝提交正式Topic Score")
    committed["score_commit"] = {
        "status": "committed",
        "pipeline_id": str(pipeline_id),
        "formula_id": UNIFIED_SCORE_FORMULA_ID,
        "committed_at": committed.get("score_calculated_at"),
        "evidence": copy.deepcopy(evidence),
        "stages": ["fitness_samples", "golden_candidates", "golden_features",
                   "golden_patterns", "account_platform_evidence", "topic_score"],
    }
    tmp = WEIGHTS.with_name(WEIGHTS.name + ".pipeline-tmp")
    tmp.write_text(json.dumps(committed, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(WEIGHTS)
    return committed


def topic_performance(con) -> dict[str, float]:
    """按 topic_id 聚合互动率 = (likes+favorites+comments)/views 的均值。

    取每篇最新一次采集记录,避免同一篇被多日重复计入。
    """
    rows = con.execute("""
        SELECT m.topic_id, m.views, m.likes, m.favorites, m.comments
        FROM note_metrics m
        JOIN (
            SELECT note_id, MAX(collected) AS latest
            FROM note_metrics GROUP BY note_id
        ) x ON m.note_id = x.note_id AND m.collected = x.latest
        WHERE m.topic_id != '' AND m.views > 0
    """).fetchall()

    agg: dict[str, list[float]] = {}
    for topic_id, views, likes, favs, comments in rows:
        eng = (likes + favs + comments) / views if views else 0.0
        agg.setdefault(topic_id, []).append(eng)
    return {tid: sum(v) / len(v) for tid, v in agg.items() if v}


def _clamp01(value, default: float=0.0) -> float:
    try:
        return max(0.0,min(1.0,float(value)))
    except (TypeError,ValueError):
        return default


def apply_unified_scores(weights: dict, bump_version: bool=False) -> dict:
    """用唯一公式重算每个选题的最终权重。

    final_score = 账号35% + 平台30% + Golden Pattern30% + 热点新颖度5%
    Golden Pattern重建、实时采集或账号表现变化后都调用本函数；不存在第二套
    “推荐分”。无证据的因子采用中性/零值，不会伪造实时热点或Golden支持。
    """
    _validate_unified_formula()
    topics=weights.get("topics",[])
    max_account=max(
        [float(t.get("account_weight",t.get("base_weight",1.0))) for t in topics] or [1.0]
    )
    if max_account <= 0:
        max_account=1.0

    platform_evidence={}
    try:
        from analyze.heat import platform_heat
        sdb = ROOT / "data" / "sample.db"
        con=sqlite3.connect(sdb) if sdb.exists() else None
    except Exception:
        con=None
    if con is not None:
        try:
            for t in topics:
                if t.get("category") == "insurance_soft":
                    platform_evidence[t["id"]]={"heat":0.0,"n":0,"dimensions":{}}
                    continue
                keyword=t.get("heat_keyword") or t.get("label") or ""
                ph=platform_heat(keyword,con) if keyword else {"heat":0,"n":0}
                platform_evidence[t["id"]]=ph
        finally:
            con.close()

    try:
        from golden import retriever
    except Exception:
        retriever=None

    for t in topics:
        account_weight=float(t.get("account_weight",t.get("base_weight",1.0)))
        # 新版完整Account Analyzer直接给0~1账号适配分；旧库无分析结果时
        # 继续兼容account_weight/max_account，不把缺失维度当0。
        model_account=t.get("account_score_model")
        account_score=(_clamp01(model_account,.5) if model_account is not None
                       else _clamp01(account_weight/max_account,.5))
        platform_pack=platform_evidence.get(t.get("id"),{})
        has_platform=bool(int(platform_pack.get("n") or 0)>0)
        # 新四维 PlatformHeat 已经是0~1，不再跨选题二次除以最大值破坏40/25/20/15。
        raw_heat=platform_pack.get("heat") if has_platform else None
        platform_score=_clamp01(raw_heat,.5) if has_platform else .5
        query=" ".join(str(x) for x in (t.get("label"),t.get("heat_keyword"),t.get("angle")) if x)
        support=(retriever.pattern_support(query,3,t.get("pattern_ids",[]))
                 if retriever is not None else {"score":0.0,"pattern_ids":[]})
        golden_score=_clamp01(support.get("score"),0.0)
        novelty_score=_clamp01(t.get("novelty_score"),.7 if t.get("source")=="discovered" else .2)
        final_score=(UNIFIED_SCORE_WEIGHTS["account"]*account_score
                     +UNIFIED_SCORE_WEIGHTS["platform"]*platform_score
                     +UNIFIED_SCORE_WEIGHTS["golden"]*golden_score
                     +UNIFIED_SCORE_WEIGHTS["novelty"]*novelty_score)
        t["account_weight"]=round(account_weight,3)
        t["account_score"]=round(account_score,3)
        t["platform_score"]=round(platform_score,3)
        t["platform_dimensions"]=platform_pack.get("dimensions",{}) if has_platform else {}
        t["platform_data_coverage"]=platform_pack.get("data_coverage",0) if has_platform else 0
        t["platform_missing_dimensions"]=platform_pack.get("missing_dimensions",[]) if has_platform else []
        t["golden_score"]=round(golden_score,3)
        t["golden_dimensions"]=support.get("breakdown",{})
        t["novelty_score"]=round(novelty_score,3)
        t["pattern_ids"]=support.get("pattern_ids",[])
        t["final_score"]=round(final_score,3)
        t["weight"]=t["final_score"]
        stats=t.setdefault("stats",{})
        stats["platform_heat"]=round(float(raw_heat),3) if raw_heat is not None else None
        stats["platform_samples"]=int(platform_pack.get("n") or 0)
        stats["platform_growth_score"]=platform_pack.get("growth_score")
        # 仅有一个模型分不等于有账号证据；必须来自真实自帖、真实受众或有效账号迁移引用。
        internal_observed=bool(t.get("account_evidence_observed"))
        external_observed=bool(has_platform or support.get("pattern_ids") or golden_score>0)
        t["evidence_gate"]={
            "internal":internal_observed,
            "external":external_observed,
            "internal_basis":t.get("account_evidence_sources",[]) or (["own_topic_profile_or_audience"] if internal_observed else []),
            "external_basis":(["platform_samples"] if has_platform else []) + (["golden_pattern"] if support.get("pattern_ids") or golden_score>0 else []),
        }
        # 单侧证据允许正式参与排序，以保留账号内容缺口和平台新趋势带来的探索选题。
        # 35/30/30/5分数不乘置信度，避免暗中改变正式公式；置信度独立展示。
        t["recommendation_eligible"]=bool(internal_observed or external_observed)
        t["evidence_confidence"]=(1.0 if internal_observed and external_observed else
                                  .7 if internal_observed or external_observed else 0.0)
        t["recommendation_status"]=("formal_cross" if internal_observed and external_observed else
                                    "formal_internal_exploration" if internal_observed else
                                    "formal_external_exploration" if external_observed else
                                    "insufficient_evidence")
        t["score_breakdown"]={
            "account":round(UNIFIED_SCORE_WEIGHTS["account"]*account_score,3),
            "platform":round(UNIFIED_SCORE_WEIGHTS["platform"]*platform_score,3),
            "golden":round(UNIFIED_SCORE_WEIGHTS["golden"]*golden_score,3),
            "novelty":round(UNIFIED_SCORE_WEIGHTS["novelty"]*novelty_score,3),
        }
        t["score_formula_id"]=UNIFIED_SCORE_FORMULA_ID
        t["account_score_formula_id"]=ACCOUNT_SCORE_FORMULA_ID
        t["platform_score_formula_id"]=PLATFORM_SCORE_FORMULA_ID
    weights["scoring_formula"]=dict(UNIFIED_SCORE_WEIGHTS)
    weights["scoring_formula_id"]=UNIFIED_SCORE_FORMULA_ID
    weights["score_calculated_at"]=datetime.now(timezone.utc).isoformat()
    weights["official_eligible_count"]=sum(1 for t in topics if t.get("recommendation_eligible"))
    if bump_version:
        weights["version"]=weights.get("version",0)+1
        weights["updated_at"]=date.today().isoformat()
    return weights


def apply_account_intelligence(weights: dict, con: sqlite3.Connection) -> dict:
    """把完整账号分析写入选题，但不在这里重复计算外层最终分。"""
    from analyze import account_intelligence
    labels={str(t.get("id")):str(t.get("label") or "") for t in weights.get("topics",[])}
    profiles=account_intelligence.topic_profiles(con,labels)
    topic_map={str(t.get("id")):t for t in weights.get("topics",[])}
    for t in weights.get("topics",[]):
        profile=profiles.get(str(t.get("id")))
        if not profile or profile.get("score") is None:
            continue
        base_score=float(profile["score"])
        transfer_sources=[]; transfer_scores=[]
        for evidence in (t.get("account_evidence") or []):
            source_id=str(evidence).split(":",1)[0].strip()
            source_profile=profiles.get(source_id)
            if source_id in topic_map and source_id != str(t.get("id")) and source_profile and source_profile.get("posts",0)>0 and source_profile.get("score") is not None:
                transfer_sources.append(source_id); transfer_scores.append(float(source_profile["score"]))
        if transfer_scores and int(profile.get("posts") or 0)==0:
            # 新选题：70%来自它明确引用的账号强项，30%来自本选题受众/缺口证据。
            base_score=.70*(sum(transfer_scores)/len(transfer_scores))+.30*base_score
        t["account_score_model"]=round(base_score,4)
        t["account_dimensions"]=profile.get("dimensions",{})
        t["account_dimension_weights"]=profile.get("dimension_weights",{})
        t["account_data_coverage"]=profile.get("coverage",0)
        t["account_confidence"]=profile.get("confidence",0)
        t["account_missing_dimensions"]=profile.get("missing_dimensions",[])
        t["account_evidence_sources"]=transfer_sources
        t["account_evidence_observed"]=bool(profile.get("evidence_observed") or transfer_sources)
        t.setdefault("stats",{})["account_posts"]=profile.get("posts",0)
    return weights


def apply_platform_heat(weights: dict, *_, **__) -> dict:
    """旧调用名兼容层；只生成内存预览，不提交正式Topic Score。"""
    return apply_unified_scores(weights,bump_version=False)

def update_weights(weights: dict, perf: dict[str, float]) -> dict:
    """只更新自己账号维度，不再改平台分或最终分。"""
    for t in weights.get("topics", []):
        t.setdefault("account_weight", float(t.get("base_weight", t.get("weight", 1.0))))

    if not perf:
        print("[分析] 暂无足够账号历史数据,account_weight 保持不变。")
        return weights

    mean_eng = sum(perf.values()) / len(perf)
    for t in weights["topics"]:
        tid = t["id"]
        if tid in perf and mean_eng > 0:
            score = perf[tid] / mean_eng
            current = float(t.get("account_weight", t.get("base_weight", 1.0)))
            new_w = current * (1 - SMOOTHING) + score * SMOOTHING
            t["account_weight"] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, new_w)), 3)
            t.setdefault("stats", {})["avg_engagement"] = round(perf[tid], 4)

    return weights

def main():
    if not DB.exists():
        print("[分析] 尚无数据库,请先运行 collect.py 采集数据。")
        return
    con = sqlite3.connect(DB)
    perf = topic_performance(con)
    con.close()

    weights = load_weights()
    weights = update_weights(weights, perf)
    weights = apply_unified_scores(weights, bump_version=False)

    print(f"[仅预览] 未写入正式权重；请完成外部采集、人工Golden确认和续跑评分。当前预览:")
    for t in weights["topics"]:
        print(f"  {t['label']:<12} weight={t['weight']:<5} "
              f"eng={t['stats']['avg_engagement']}")


if __name__ == "__main__":
    main()
