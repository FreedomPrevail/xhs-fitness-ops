from __future__ import annotations
import json
from . import repository, discovery_agent, feature_analyst, admission, pattern_agent, hydrate
from .revalidate import revalidate_golden


def _pending_score_state(reason: str) -> dict:
    """Golden只更新证据；正式Topic Score等待完整Pipeline原子提交。"""
    return {"status":"pending_pipeline","reason":reason,
            "message":"Golden证据已更新；正式Topic Score尚未改变"}

def discover(n=10): return {"ok":True,"candidates":discovery_agent.discover(n)}
def human_select(note_ref,reason="人工判断值得学习",content_type="unknown"): return {"ok":True,"candidate":discovery_agent.human_select(note_ref,reason,content_type)}
def analyze(cfg,limit=20):
    h=hydrate.hydrate_pending(limit)
    r=feature_analyst.analyze_pending(cfg,limit); a=admission.admit_featured(limit*2); p=pattern_agent.rebuild_patterns()
    return {"ok":True,"hydrate":h,"feature_analysis":r,"admission":a,"patterns":p,
            "topic_score":_pending_score_state("golden_analyzed")}
def overview():
    candidates=repository.list_candidates(100)
    samples=repository.list_samples(True)
    patterns=repository.list_patterns()
    # 0813旧Pattern没有媒介证据分层；首次打开Golden页面时做一次确定性迁移。
    if samples and (not patterns or any("media_counts" not in (p.get("pattern") or {}) for p in patterns)):
        patterns=pattern_agent.rebuild_patterns()
    return {
      "ok":True,
      "ui_version":"golden-account-v3",
      "candidates":candidates,
      "samples":samples,
      "patterns":patterns,
      "counts":{
        "candidates":len(candidates),
        "human":sum("human" in str(x.get("source_type") or "").split("+") for x in candidates),
        "agent":sum("agent" in str(x.get("source_type") or "").split("+") for x in candidates),
        "pending_detail":sum(x.get("content_type")!="video" and not str(x.get("body") or "").strip()
                             and x.get("status") in ("pending","featured") for x in candidates),
        "featured":sum(bool(x.get("has_feature")) for x in candidates),
        "golden":len(samples),
        "image_text":sum(x.get("content_type")=="image_text" for x in samples),
        "video":sum(x.get("content_type")=="video" for x in samples),
        "unknown_media":sum(x.get("content_type")=="unknown" for x in samples),
        "patterns":len(patterns),
        "visual_covers":sum(bool((x.get("features") or {}).get("cover",{}).get("visual_observed")) for x in samples),
      },
    }
def rebuild():
    p=pattern_agent.rebuild_patterns()
    return {"ok":True,"patterns":p,"topic_score":_pending_score_state("golden_patterns_rebuilt")}
def revalidate(limit=10):
    """Read due Golden metrics after a user action, then rebuild Pattern weights."""
    r=revalidate_golden(limit)
    p=pattern_agent.rebuild_patterns() if r.get("revalidated") else repository.list_patterns()
    score_state = (_pending_score_state("golden_revalidated")
                   if r.get("revalidated") else
                   {"status":"unchanged","reason":"no_live_update",
                    "message":"本轮没有有效热度更新；Pattern 与正式 Topic Score 均未改变"})
    return {"ok":not bool(r.get("hard_stop")),"revalidation":r,"patterns":p,
            "topic_score":score_state,"error":(r.get("hard_stop") or {}).get("error","")}
def classify_media(candidate_id: str,content_type: str):
    c=repository.classify_unknown_media(candidate_id,content_type)
    discovery_agent.persist_sample_content_type(c.get("url") or c.get("note_id") or c.get("title",""),content_type,"manual")
    p=pattern_agent.rebuild_patterns()
    return {"ok":True,"candidate":c,"patterns":len(p),
            "topic_score":_pending_score_state("golden_media_classified")}
def feedback(**kwargs):
    r=repository.record_feedback(**kwargs); p=pattern_agent.rebuild_patterns()
    return {"ok":True,"feedback":r,"patterns":p,
            "topic_score":_pending_score_state("golden_feedback_updated")}


def sync_performance_from_metrics(metrics_db) -> dict:
    """Close the loop after creator metrics collection.
    A published content_record points to its local draft JSON; that JSON keeps golden_usage_id.
    The latest note metrics become feedback for the Golden samples used by that draft.
    """
    import json, sqlite3
    from pathlib import Path
    db=Path(metrics_db)
    if not db.exists(): return {"synced":0}
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not {"content_records","note_metrics"}.issubset(tables): con.close(); return {"synced":0}
    recs=con.execute("SELECT local_path,platform_note_id,title,topic_id FROM content_records WHERE status='published'").fetchall()
    synced=0; skipped_low_views=0; errors=[]
    for r in recs:
        try:
            path=Path(r["local_path"] or "")
            if not path.exists(): continue
            d=json.loads(path.read_text(encoding="utf-8")); usage=d.get("golden_usage_id") or ""
            if not usage: continue
            if r["platform_note_id"]:
                m=con.execute("SELECT views,likes,favorites,comments,shares,rise_fans,avg_view_time_seconds,ctr,completion_rate,top_source FROM note_metrics WHERE note_id=? ORDER BY collected DESC LIMIT 1",(r["platform_note_id"],)).fetchone()
            else:
                m=con.execute("SELECT views,likes,favorites,comments,shares,rise_fans,avg_view_time_seconds,ctr,completion_rate,top_source FROM note_metrics WHERE title=? AND topic_id=? ORDER BY collected DESC LIMIT 1",(r["title"],r["topic_id"])).fetchone()
            if not m: continue
            if int(m["views"] or 0) < 100:
                skipped_low_views += 1
                continue
            repository.record_feedback(usage,int(m["views"] or 0),int(m["likes"] or 0),int(m["favorites"] or 0),int(m["comments"] or 0),
                                       shares=int(m["shares"] or 0),rise_fans=int(m["rise_fans"] or 0),
                                       avg_view_time_seconds=m["avg_view_time_seconds"],ctr=m["ctr"],
                                       completion_rate=m["completion_rate"],top_source=str(m["top_source"] or "")); synced+=1
        except Exception as e: errors.append(str(e)[:160])
    con.close()
    patterns=pattern_agent.rebuild_patterns() if synced else repository.list_patterns()
    score_state=_pending_score_state("golden_performance_synced") if synced else {}
    return {"synced":synced,"skipped_low_views":skipped_low_views,"errors":errors,
            "patterns":len(patterns),"topic_score":score_state}
