from __future__ import annotations
import math, re, sqlite3
from datetime import date, datetime
from pathlib import Path
from . import repository

ROOT=Path(__file__).resolve().parent.parent
SAMPLE_DB=ROOT/"data"/"sample.db"


def _freshness(published_at: str) -> float:
    s=(published_at or "").strip()[:10]
    try: days=max(0,(date.today()-datetime.strptime(s,"%Y-%m-%d").date()).days)
    except Exception: return 0.5
    return max(0.0, min(1.0, math.exp(-days/90)))


def _note_id(url: str) -> str:
    m=re.search(r"/(?:explore|discovery/item|search_result)/([0-9a-fA-F]{12,})", url or "")
    return m.group(1) if m else ""


def load_latest_samples(limit: int=200) -> list[dict]:
    if not SAMPLE_DB.exists(): return []
    con=sqlite3.connect(SAMPLE_DB); con.row_factory=sqlite3.Row
    cols={r[1] for r in con.execute("PRAGMA table_info(samples)")}
    def c(name, fallback): return name if name in cols else fallback
    body=c("body", "''"); nid=c("note_id", "''"); cover=c("cover_url", "''"); detail_json=c("detail_json", "''")
    content_type=c("content_type", "'unknown'"); content_type_source=c("content_type_source", "'unknown'")
    rows=con.execute(f"""SELECT url,MAX(title) title,MAX(author) author,MAX(keyword) keyword,MAX(published_at) published_at,
      MAX(likes_num) likes_num,MAX(COALESCE(collects_num,0)) collects_num,MAX(COALESCE(comments_num,0)) comments_num,
      MAX(COALESCE(detail_fetched,0)) detail_fetched,MAX({body}) body,MAX({nid}) note_id,MAX({cover}) cover_url,
      MAX({detail_json}) detail_json,
      CASE WHEN SUM(CASE WHEN {content_type}='video' THEN 1 ELSE 0 END)>0 THEN 'video'
           WHEN SUM(CASE WHEN {content_type}='image_text' THEN 1 ELSE 0 END)>0 THEN 'image_text'
           ELSE 'unknown' END content_type,MAX({content_type_source}) content_type_source
      FROM samples WHERE collected=(SELECT MAX(collected) FROM samples) GROUP BY url ORDER BY likes_num DESC LIMIT ?""",(int(limit),)).fetchall()
    con.close()
    out=[]
    for r in rows:
        item=dict(r)
        stored=repository.normalize_content_type(item.get("content_type"))
        item["content_type"]=stored if stored!="unknown" else repository.infer_content_type(item,"unknown")
        out.append(item)
    return out


def score_rows(rows: list[dict]) -> list[dict]:
    if not rows: return []
    interactions=[int(r.get("likes_num") or 0)+2*int(r.get("collects_num") or 0)+3*int(r.get("comments_num") or 0) for r in rows]
    max_log=max([math.log1p(x) for x in interactions] or [1.0]) or 1.0
    out=[]
    for r, inter in zip(rows,interactions):
        likes=max(int(r.get("likes_num") or 0),1); collects=int(r.get("collects_num") or 0); comments=int(r.get("comments_num") or 0)
        perf=math.log1p(inter)/max_log
        collect_quality=min(1.0,(collects/likes)/0.65) if r.get("detail_fetched") else 0.45
        comment_quality=min(1.0,(comments/likes)/0.08) if r.get("detail_fetched") else 0.45
        fresh=_freshness(r.get("published_at", ""))
        detail=1.0 if r.get("detail_fetched") else 0.45
        score=.42*perf+.20*collect_quality+.10*comment_quality+.18*fresh+.10*detail
        reasons=[]
        if perf>=.75: reasons.append("平台互动表现位于高位")
        if collect_quality>=.7: reasons.append("收藏/点赞比高，知识价值或复用价值强")
        if comment_quality>=.7: reasons.append("评论密度高，讨论意愿强")
        if fresh>=.75: reasons.append("发布时间较新")
        if r.get("detail_fetched"): reasons.append("已抓到详情，可分析正文而非只看标题")
        d={**r,"note_id":r.get("note_id") or _note_id(r.get("url","")),"likes":likes,"collects":collects,"comments":comments,"candidate_score":round(score,4),"candidate_reasons":reasons or ["综合表现进入候选排名"]}
        out.append(d)
    return sorted(out,key=lambda x:x["candidate_score"],reverse=True)


def discover(n: int=10) -> list[dict]:
    ranked=score_rows(load_latest_samples(max(100,n*10)))
    selected=[]; seen=set()
    for r in ranked:
        # 纯视频无正文的候选从源头跳过,与 hydrate 的 video_metadata_only 判定一致,
        # 避免 Feature 弱证据/失败卡住 B3 门禁。
        if repository.normalize_content_type(r.get("content_type"))=="video" and not (r.get("body") or "").strip():
            continue
        key=r.get("note_id") or r.get("url") or r.get("title")
        if not key or key in seen: continue
        seen.add(key)
        selected.append(repository.upsert_candidate(r,"agent",{"agent":"discovery_agent","reasons":r["candidate_reasons"],"scoring":"performance+collect_quality+comment_quality+freshness+detail"},r["candidate_score"]))
        if len(selected)>=n: break
    return selected


def human_select(note_ref: str, reason: str="人工判断值得学习", content_type: str="unknown") -> dict:
    rows=load_latest_samples(500)
    hit=next((r for r in rows if note_ref in (r.get("url"),r.get("note_id")) or note_ref==r.get("title")),None)
    if not hit: raise ValueError("未在 sample.db 找到该帖子；请先通过热门搜索/选题科学化采集它")
    hit["note_id"]=hit.get("note_id") or _note_id(hit.get("url",""))
    requested=repository.normalize_content_type(content_type)
    if requested != "unknown":
        hit["content_type"]=requested; persist_sample_content_type(note_ref,requested,"manual")
    return repository.upsert_candidate(hit,"human",{"selected_by":"operator","reason":reason},1.0)


def persist_sample_content_type(note_ref: str, content_type: str, source: str="manual") -> None:
    content_type=repository.normalize_content_type(content_type)
    if content_type=="unknown" or not SAMPLE_DB.exists(): return
    con=sqlite3.connect(SAMPLE_DB)
    cols={r[1] for r in con.execute("PRAGMA table_info(samples)")}
    if not {"content_type","content_type_source"}.issubset(cols):
        con.close(); return
    con.execute("""UPDATE samples SET content_type=?,content_type_source=?
                   WHERE url=? OR note_id=? OR title=?""",
                (content_type,source,note_ref,note_ref,note_ref))
    con.commit(); con.close()
