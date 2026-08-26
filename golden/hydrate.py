from __future__ import annotations
import json, random, sqlite3, time
from pathlib import Path
from collect import collect, sample_collect
from . import repository

ROOT=Path(__file__).resolve().parent.parent
SAMPLE_DB=ROOT/"data"/"sample.db"

def _detail_to_fields(payload) -> dict:
    content_type,content_type_source=sample_collect.detect_content_type(payload)
    d=sample_collect._unwrap_detail(payload)
    if not isinstance(d,dict): return {"raw_detail":payload}
    pick=sample_collect._pick
    cover=pick(d,["cover_url","cover","image","image_url","first_image"],"")
    if isinstance(cover,list): cover=cover[0] if cover else ""
    if isinstance(cover,dict): cover=cover.get("url") or cover.get("urlDefault") or ""
    if content_type=="unknown": content_type,content_type_source=sample_collect.detect_content_type(d)
    if content_type_source!="unknown": content_type_source="detail:"+content_type_source
    return {
      "note_id":str(pick(d,["note_id","noteId","id"],"")),
      "body":str(pick(d,["body","desc","description","content","text","正文"],"")),
      "cover_url":str(cover or ""),
      "likes":sample_collect.parse_count(pick(d,["likes","like_count","likeCount","点赞","点赞数"],0)),
      "collects":sample_collect.parse_count(pick(d,["collects","favorites","favorite_count","collect_count","收藏","收藏数"],0)),
      "comments":sample_collect.parse_count(pick(d,["comments","comment_count","commentCount","评论","评论数"],0)),
      "content_type":content_type,"content_type_source":content_type_source,
      "raw_detail":d,
    }

def _update_sample(url: str, f: dict):
    if not SAMPLE_DB.exists(): return
    con=sample_collect.init_db()
    con.execute("""UPDATE samples SET note_id=CASE WHEN ?!='' THEN ? ELSE note_id END,
      body=CASE WHEN ?!='' THEN ? ELSE body END, cover_url=CASE WHEN ?!='' THEN ? ELSE cover_url END,
      likes_num=CASE WHEN ?>0 THEN ? ELSE likes_num END, collects_num=CASE WHEN ?>0 THEN ? ELSE collects_num END,
      comments_num=CASE WHEN ?>0 THEN ? ELSE comments_num END,
      content_type=CASE WHEN ?!='unknown' THEN ? ELSE content_type END,
      content_type_source=CASE WHEN ?!='unknown' THEN ? ELSE content_type_source END,
      detail_fetched=1,detail_collected=?,detail_json=? WHERE url=?""",
      (f.get("note_id",""),f.get("note_id",""),f.get("body",""),f.get("body",""),f.get("cover_url",""),f.get("cover_url",""),
       int(f.get("likes") or 0),int(f.get("likes") or 0),int(f.get("collects") or 0),int(f.get("collects") or 0),int(f.get("comments") or 0),int(f.get("comments") or 0),
       f.get("content_type","unknown"),f.get("content_type","unknown"),f.get("content_type_source","unknown"),f.get("content_type_source","unknown"),
       __import__('datetime').date.today().isoformat(),json.dumps(f.get("raw_detail",{}),ensure_ascii=False),url))
    con.commit(); con.close()

def hydrate_pending(limit: int=20) -> dict:
    all_pending=repository.pending_candidates(limit*3)
    # 没有本地视频文件时，视频不再反复请求详情；它可以直接做metadata-only弱Feature。
    skipped_video=[c["candidate_id"] for c in all_pending
                   if c.get("content_type")=="video" and not (c.get("body") or "").strip()]
    pending=[c for c in all_pending if c.get("content_type")!="video"
             and not (c.get("body") or "").strip()][:limit]
    hydrated=[]; errors=[]; hard_stop=None
    for i,c in enumerate(pending):
        url=c.get("url") or ""
        error_base={"candidate_id":c["candidate_id"],"title":c.get("title","")[:80],"url":url,"note_id":c.get("note_id","")}
        if not url: errors.append({**error_base,"code":"MISSING_URL","error":"缺少原帖 URL，无法打开详情"}); continue
        if i: time.sleep(2.5+random.uniform(0,1.5))
        try: payload,err=collect.run_cli(["note",url],retries=0)
        except RuntimeError as e: payload,err=None,{"code":"RUNTIME","message":str(e)}
        if err:
            errors.append({**error_base,"code":err.get("code",""),"error":str(err.get("message",err))[:300]})
            if sample_collect._must_stop_batch(err): hard_stop=errors[-1]; break
            continue
        f=_detail_to_fields(payload)
        if not str(f.get("body") or "").strip():
            if f.get("content_type")=="video":
                # 已用本来就发生的详情请求确认是视频；直接转弱Feature，不再要求正文。
                repository.update_candidate_detail(c["candidate_id"],f); _update_sample(url,f); hydrated.append(c["candidate_id"])
                continue
            errors.append({**error_base,"code":"EMPTY_BODY","error":"详情接口返回成功，但没有正文；请检查 URL 的 xsec_token/登录态"})
            continue
        repository.update_candidate_detail(c["candidate_id"],f); _update_sample(url,f); hydrated.append(c["candidate_id"])
    return {"attempted":len(pending),"hydrated":len(hydrated),"errors":errors,"hard_stop":hard_stop,
            "video_metadata_only":len(skipped_video),"video_candidate_ids":skipped_video}
