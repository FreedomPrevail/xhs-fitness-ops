#!/usr/bin/env python3
"""从实时高赞标题 × Golden Pattern归纳新选题并进入统一权重池。

流程(全程 CLI):
  ① 读 sample.db 里的高赞帖标题(来自 opencli search)
  ② 加入Golden Pattern的受众/搜索/结构证据，归纳具体热点选题
  ③ 仅合并真正同一选题，保留旧大类下的新热点角度
  ④ 保存热点证据、Pattern ID与新颖度，交给唯一权重公式计算
generate.pick_topic 读的是本地 data/topic_weights.json,新选题进入完整提交后下次即可被选中。
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from content import generate  # 复用 CLAUDE 路径
from golden.llm import call_json, is_configured
from golden import repository
from analyze import account_intelligence, heat
from compliance import policy as compliance_policy

DB = ROOT / "data" / "sample.db"
WEIGHTS = ROOT / "data" / "topic_weights.json"
WEIGHTS_SEED = ROOT / "config" / "topic_weights.example.json"


def _read_weights() -> dict:
    path = WEIGHTS if WEIGHTS.exists() else WEIGHTS_SEED
    return json.loads(path.read_text(encoding="utf-8"))


def _account_context(limit: int=8) -> dict:
    """自己账号已观测数据产生的机会信号；没有数据时返回明确missing。"""
    db=ROOT/"data"/"metrics.db"
    if not db.exists(): return {"status":"missing","opportunities":[],"timing":{}}
    try:
        weights=_read_weights()
        con=sqlite3.connect(db)
        result=account_intelligence.report(con,weights.get("topics",[]))
        con.close()
        return {"status":"observed_or_partial","opportunities":result.get("opportunities",[])[:limit],
                "audience":result.get("audience",{}),"timing":result.get("timing",{}),
                "data_center":result.get("data_center",{}),
                "operations":result.get("operations",{}),
                "limitations":result.get("limitations",[])}
    except Exception as exc:
        return {"status":"error","opportunities":[],"timing":{},"error":str(exc)[:160]}


def top_titles(limit: int = 30) -> list[tuple[int, str]]:
    """取样本库里点赞最高的标题。"""
    if not DB.exists():
        return []
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT likes_num, title FROM samples "
        "WHERE collected=(SELECT MAX(collected) FROM samples) "
        "ORDER BY likes_num DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return rows


def _external_opportunity_context(limit: int=10) -> list[dict]:
    """把外部标题先聚合成机会卡，避免Topic Agent只看点赞标题猜趋势。"""
    if not DB.exists():
        return []
    con=sqlite3.connect(DB)
    try:
        keywords=[str(r[0]) for r in con.execute(
            "SELECT DISTINCT keyword FROM samples WHERE collected=(SELECT MAX(collected) FROM samples) "
            "AND COALESCE(keyword,'')!='' LIMIT 40").fetchall()]
        cards=[]
        for keyword in keywords:
            pack=heat.platform_heat(keyword,con)
            titles=con.execute(
                "SELECT title,likes_num,collects_num,comments_num FROM samples WHERE keyword=? "
                "AND collected=(SELECT MAX(collected) FROM samples WHERE keyword=?) "
                "ORDER BY likes_num DESC LIMIT 4",(keyword,keyword)).fetchall()
            if int(pack.get("n") or 0)<=0:
                continue
            cards.append({
                "opportunity_id":"platform:"+keyword,
                "keyword":keyword,
                "platform_heat":pack.get("heat"),
                "dimensions":pack.get("dimensions",{}),
                "data_coverage":pack.get("data_coverage",0),
                "growth_score":pack.get("growth_score"),
                "sample_n":pack.get("n",0),
                "common_phrases":pack.get("common_phrases",[]),
                "evidence_titles":[{"title":r[0],"likes":r[1],"collects":r[2],"comments":r[3]} for r in titles],
            })
        return sorted(cards,key=lambda x:(float(x.get("platform_heat") or 0),int(x.get("sample_n") or 0)),reverse=True)[:limit]
    finally:
        con.close()


def _golden_pattern_context(limit: int=8) -> list[dict]:
    """给Discovery Agent提供Pattern证据，不传Golden长原文。"""
    out=[]
    for p in repository.list_patterns()[:max(0,int(limit))]:
        body=p.get("pattern",{}) if isinstance(p.get("pattern"),dict) else {}
        def values(key,n=3):
            return [str(x.get("value")) for x in (body.get(key,[]) or [])[:n]
                    if isinstance(x,dict) and x.get("value")]
        out.append({
          "pattern_id":p.get("pattern_id"),"segment":p.get("segment_key"),
          "effective_weight":p.get("effective_weight",0),
          "media_counts":body.get("media_counts",{}),
          "audience_goals":values("audience_goals"),
          "search_queries":values("search_queries"),
          "content_structures":values("content_structures",2),
        })
    return out


def discover_topics(n: int = 5, model_config: dict | None = None) -> list[dict]:
    """用实时标题与Golden Pattern共同生成新选题；未配置时兼容Claude CLI。"""
    titles = top_titles(30)
    account_context=_account_context()
    if not titles and not account_context.get("opportunities"):
        return []
    # 用数字编号而非 "- " 前缀:实测 claude CLI 会把行首 "- " 误当标记而"看不到"数据
    title_list = "\n".join(f"{i}. {t}（{k}赞）" for i, (k, t) in enumerate(titles, 1)) or "(本轮无实时标题，只能使用账号机会证据)"
    patterns=_golden_pattern_context()
    external_opportunities=_external_opportunity_context()
    pattern_rule=("必须把实时热点与相关Golden Pattern交叉组合，并只引用上面真实存在的Pattern ID；"
                  if patterns else
                  "当前还没有Golden Pattern，本轮只从实时热点发现选题并令pattern_ids为空；")
    prompt = (
        "以下是我已经采集好的小红书健身类高赞笔记标题数据(就在下面,数据完整,直接基于它分析,不要反问、不要索要更多数据):\n\n"
        + title_list +
        "\n\n以下是把外部帖子按关键词聚合后的平台机会卡。四维分数、覆盖率和增长缺失必须原样遵守：\n"
        + json.dumps(external_opportunities,ensure_ascii=False) +
        "\n\n以下是当前Golden Pattern证据，只能用于判断受众、搜索意图和可迁移结构：\n"
        + json.dumps(patterns,ensure_ascii=False) +
        "\n\n以下是自己账号分析证据。observed_or_partial不代表所有字段都真实可用，必须遵守limitations：\n"
        + json.dumps(account_context,ensure_ascii=False) +
        "\n\n任务:基于上面这些真实爆款标题,归纳出 " + str(n) +
        " 个【当下最火且适合账号的新选题】。" + pattern_rule +
        "选题要具体(不要泛如'健身'),互不重复，也不要把旧大类名称直接当新选题。\n"
        "必须是健身/运动/减脂/体态/拉伸/塑形/力量训练/饮食等运动健康相关选题,"
        "绝对不要输出编程、科技、营销、娱乐等与运动无关的选题。\n"
        "每个选题给出:label(具体选题名≤14字)、category(只能填fitness_value或health_risk)、"
        "heat_keyword(用于搜索的2-10字词)、angle(原创角度)、audience(对象)、intent(search或feed)、"
        "trend_evidence(支撑它的2-4个真实标题关键词/短语)、pattern_ids(实际使用的Pattern ID)、"
        "external_evidence_ids(实际使用的平台机会卡ID)、"
        "account_evidence(实际使用的账号选题ID/机会理由；无账号证据则空数组)、recommended_periods(只引用上面已有时段证据)、"
        "novelty_score(0-1，和现有常规选题越不同越高)。不得输出title_patterns模板。\n"
        "只输出 JSON,第一个字符就是 {,不要任何前言:\n"
        '{"topics":[{"label":"...","category":"fitness_value","heat_keyword":"...","angle":"...","audience":{"goal":"..."},"intent":"search","trend_evidence":["..."],"external_evidence_ids":["platform:..."],"pattern_ids":["gp_..."],"account_evidence":["topic_id:理由"],"recommended_periods":[],"novelty_score":0.8}]}')
    cfg=model_config or {}
    if is_configured(cfg, role="topic"):
        data=call_json(prompt,cfg,role="topic")
        topics=data.get("topics",[]) if isinstance(data,dict) else []
        normalized=[_normalize_discovered(x) for x in topics if isinstance(x,dict)]
        normalized=_validate_evidence(normalized,account_context,patterns,external_opportunities)
        return [x for x in normalized if compliance_policy.evaluate(x.get("label",""),x.get("angle",""))["status"]!="BLOCK"][:n]

    # 兼容旧环境：无网页模型配置时才走本地 Claude CLI。
    proc = subprocess.run(
        [generate.CLAUDE, "-p", "--bare", "--tools", ""],
        input=prompt, capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace")
    raw = (proc.stdout or "").strip()
    if not raw:
        detail = (proc.stderr or "").strip()[:200]
        raise RuntimeError(f"Claude 未返回内容{(': ' + detail) if detail else ''}")
    s, e = raw.find("{"), raw.rfind("}")            # 抽 JSON 段(容忍 ```json 围栏)
    if s < 0 or e < 0 or e < s:
        raise RuntimeError(f"Claude 未返回 JSON: {raw[:200]}")
    try:
        data = json.loads(raw[s:e + 1])
    except json.JSONDecodeError:
        # 发现新选题只是增强步骤；让 pipeline 捕获 RuntimeError 后跳过，
        # 不要把已完成的平台采集和权重计算一起判成失败。
        raise RuntimeError(f"Claude 返回的选题 JSON 格式无效: {raw[:200]}") from None
    normalized=[_normalize_discovered(x) for x in data.get("topics", []) if isinstance(x,dict)]
    normalized=_validate_evidence(normalized,account_context,patterns,external_opportunities)
    return [x for x in normalized if compliance_policy.evaluate(x.get("label",""),x.get("angle",""))["status"]!="BLOCK"][:n]


def _normalize_discovered(value: dict) -> dict:
    """限制模型字段并稳定化新颖度/证据结构。"""
    def score(v,default=.6):
        try: return max(0.0,min(1.0,float(v)))
        except (TypeError,ValueError): return default
    return {
      "label":str(value.get("label") or "").strip()[:14],
      "category":value.get("category") if value.get("category") in ("fitness_value","health_risk") else "fitness_value",
      "heat_keyword":str(value.get("heat_keyword") or value.get("label") or "").strip()[:20],
      "angle":str(value.get("angle") or "").strip()[:120],
      "audience":value.get("audience") if isinstance(value.get("audience"),dict) else {},
      "intent":str(value.get("intent") or "mixed").strip()[:20],
      "trend_evidence":[str(x).strip()[:80] for x in (value.get("trend_evidence") or []) if str(x).strip()][:4],
      "external_evidence_ids":[str(x).strip()[:100] for x in (value.get("external_evidence_ids") or []) if str(x).strip()][:5],
      "pattern_ids":[str(x).strip() for x in (value.get("pattern_ids") or []) if str(x).strip()][:5],
      "account_evidence":[str(x).strip()[:160] for x in (value.get("account_evidence") or []) if str(x).strip()][:5],
      "recommended_periods":[x for x in (value.get("recommended_periods") or []) if isinstance(x,(str,dict))][:3],
      "novelty_score":score(value.get("novelty_score")),
    }


def _validate_evidence(topics: list[dict], account_context: dict,
                       patterns: list[dict], external_opportunities: list[dict]) -> list[dict]:
    """只保留真实存在的证据引用，并标记内外融合状态；不让模型自造ID。"""
    account_ids={str(x.get("topic_id")) for x in (account_context.get("opportunities") or []) if x.get("topic_id")}
    pattern_ids={str(x.get("pattern_id")) for x in patterns if x.get("pattern_id")}
    external_ids={str(x.get("opportunity_id")) for x in external_opportunities if x.get("opportunity_id")}
    for topic in topics:
        topic["pattern_ids"]=[x for x in topic.get("pattern_ids",[]) if x in pattern_ids]
        topic["external_evidence_ids"]=[x for x in topic.get("external_evidence_ids",[]) if x in external_ids]
        valid_account=[]
        for item in topic.get("account_evidence",[]):
            if str(item).split(":",1)[0].strip() in account_ids:
                valid_account.append(item)
        topic["account_evidence"]=valid_account
        has_internal=bool(valid_account)
        has_external=bool(topic.get("external_evidence_ids") or topic.get("pattern_ids"))
        topic["discovery_evidence_mode"]=("cross" if has_internal and has_external else
                                          "internal_only" if has_internal else
                                          "external_only" if has_external else "insufficient")
        # 单侧真实证据也可产生有价值的新选题：内部缺口型或外部新趋势型。
        # 只有两侧都没有真实引用时才拒绝进入正式评分。
        topic["discovery_formal_eligible"]=bool(has_internal or has_external)
        topic["discovery_confidence"]=(1.0 if has_internal and has_external else
                                         .7 if has_internal or has_external else 0.0)
    return topics


def _slug(label: str, existing: set) -> str:
    """给新选题生成英文 id(拼音略,用序号兜底)。"""
    base = "disc_" + re.sub(r"[^a-z0-9]", "", label.lower()) or "disc"
    i, sid = 1, base
    while sid in existing:
        sid = f"{base}_{i}"; i += 1
    return sid


def _merge_evidence(existing: dict, new_topic: dict):
    """同一选题只更新实时/Golden证据，不再生成或合并旧标题模板。"""
    existing["heat_keyword"]=(new_topic.get("heat_keyword") or existing.get("heat_keyword") or existing.get("label"))
    existing["novelty_score"]=max(float(existing.get("novelty_score") or 0),float(new_topic.get("novelty_score") or 0))
    existing["trend_evidence"]=list(dict.fromkeys([
        *(existing.get("trend_evidence") or []),*(new_topic.get("trend_evidence") or [])
    ]))[:6]
    existing["pattern_ids"]=list(dict.fromkeys([
        *(existing.get("pattern_ids") or []),*(new_topic.get("pattern_ids") or [])
    ]))[:6]
    existing["external_evidence_ids"]=list(dict.fromkeys([
        *(existing.get("external_evidence_ids") or []),*(new_topic.get("external_evidence_ids") or [])
    ]))[:6]
    existing["account_evidence"]=list(dict.fromkeys([
        *(existing.get("account_evidence") or []),*(new_topic.get("account_evidence") or [])
    ]))[:6]
    if new_topic.get("recommended_periods"):
        existing["recommended_periods"]=new_topic.get("recommended_periods")[:3]


def merge_into_weights(new_topics: list[dict], default_weight: float = 1.2,
                       weights: dict | None = None) -> dict:
    """在内存中去重并补全新选题；正式配置只能由完整Pipeline提交。"""
    w = weights if isinstance(weights, dict) else _read_weights()
    existing_labels = {t["label"] for t in w["topics"]}
    existing_ids = {t["id"] for t in w["topics"]}
    # 只合并真正近似同名的选题；共享“健身/训练/减脂”等词不再吞掉热点角度。
    import re as _re
    def _keywords(s: str) -> set:
        """提取 2-3 字中文关键词，用于语义去重"""
        s = _re.sub(r"[^\u4e00-\u9fff]", "", s)
        return {s[i:i+n] for n in (2, 3) for i in range(len(s) - n + 1)}

    def _find_duplicate(new_topic: dict) -> int | None:
        """完全同名或2/3字指纹Jaccard>=0.82才合并。"""
        new_label = (new_topic.get("label") or "").strip()
        new_kw = _keywords(new_label)
        if not new_kw:
            return None
        normalized=_re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]","",new_label).lower()
        for i,t in enumerate(w["topics"]):
            old_label=str(t.get("label") or "").strip()
            old_normalized=_re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]","",old_label).lower()
            if normalized == old_normalized:
                return i
            old_kw=_keywords(old_label)
            union=new_kw|old_kw
            similarity=len(new_kw&old_kw)/max(1,len(union))
            if similarity >= .82:
                return i
        return None

    merged_count = 0
    added = []
    for nt in new_topics:
        label = (nt.get("label") or "").strip()
        if not label or label in existing_labels:
            if label in existing_labels:
                idx=next((i for i,t in enumerate(w["topics"]) if t.get("label")==label),None)
                if idx is not None:
                    _merge_evidence(w["topics"][idx],nt); merged_count+=1
            continue
        dup_idx = _find_duplicate(nt)
        if dup_idx is not None:
            _merge_evidence(w["topics"][dup_idx], nt)
            merged_count += 1
            continue
        cat = nt.get("category") if nt.get("category") in ("fitness_value", "health_risk") else "fitness_value"
        sid = _slug(label, existing_ids)
        topic = {
            "id": sid, "category": cat, "label": label,
            "weight": default_weight,
            "base_weight": default_weight,
            "account_weight": default_weight,
            "platform_score": 0.5,
            "golden_score": 0.0,
            "novelty_score": nt.get("novelty_score",.6),
            "final_score": default_weight,
            "heat_keyword": nt.get("heat_keyword") or label,
            "angle":nt.get("angle", ""),
            "trend_evidence":nt.get("trend_evidence",[]),
            "external_evidence_ids":nt.get("external_evidence_ids",[]),
            "pattern_ids":nt.get("pattern_ids",[]),
            "account_evidence":nt.get("account_evidence",[]),
            "discovery_evidence_mode":nt.get("discovery_evidence_mode","insufficient"),
            "discovery_formal_eligible":bool(nt.get("discovery_formal_eligible")),
            "recommended_periods":nt.get("recommended_periods",[]),
            "topic_brief_seed":{"angle":nt.get("angle", ""),"audience":nt.get("audience",{}),"intent":nt.get("intent","mixed"),"keywords":[nt.get("heat_keyword") or label]},
            "stats": {"posts": 0, "avg_ctr": 0.0, "avg_engagement": 0.0},
            "source": "unified_account_golden_discovered",
        }
        w["topics"].append(topic)
        existing_ids.add(sid); existing_labels.add(label)
        added.append(topic)
    return {"added": added, "merged": merged_count, "total_topics": len(w["topics"]),
            "weights": w, "committed": False}


def main():
    print("[选题发现] 从高赞帖归纳新选题…")
    topics = discover_topics(5)
    print(f"  Claude 归纳出 {len(topics)} 个候选")
    r = merge_into_weights(topics)
    print(f"  仅预览：新增 {len(r['added'])} 个,合并 {r.get('merged', 0)} 个,选题池共 {r['total_topics']} 个；未写入正式权重")
    for t in r["added"]:
        print(f"   + {t['label']} [{t['category']}] 热词={t['heat_keyword']} 新颖度={t['novelty_score']}")


if __name__ == "__main__":
    main()
