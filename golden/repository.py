from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "golden.db"
POOL_JSON = ROOT / "data" / "golden_pool.json"
PATTERN_JSON = ROOT / "data" / "golden_patterns.json"
CONTENT_TYPES = {"image_text", "video", "unknown"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_content_type(value) -> str:
    """把页面/适配器的不同叫法归一为三种稳定媒介类型。"""
    text=str(value or "").strip().lower().replace("-", "_")
    if text in {"video", "视频", "短视频", "movie"} or "video" in text or "视频" in text:
        return "video"
    if text in {"image_text", "image", "images", "note", "normal", "图文", "图片", "图文笔记"}:
        return "image_text"
    if "image" in text or "图文" in text:
        return "image_text"
    return "unknown"


def infer_content_type(payload, fallback: str="unknown") -> str:
    """只根据可观察字段识别媒介；不凭标题猜视频/图文。"""
    fallback=normalize_content_type(fallback)
    queue=[payload]; seen=0
    while queue and seen < 40:
        cur=queue.pop(0); seen+=1
        if isinstance(cur,str):
            raw=cur.strip()
            if raw.startswith(("{","[")):
                try: queue.append(json.loads(raw))
                except Exception: pass
            continue
        if isinstance(cur,list):
            queue.extend(cur[:10]); continue
        if not isinstance(cur,dict):
            continue
        for key in ("content_type","media_type","note_type","noteType","type"):
            detected=normalize_content_type(cur.get(key))
            if detected != "unknown": return detected
        if any(cur.get(k) for k in ("video_url","videoUrl","video_duration","duration_ms","duration")):
            return "video"
        for key in ("image_list","imageList","images","image_info_list","imageInfoList"):
            if isinstance(cur.get(key),list) and cur[key]: return "image_text"
        for key in ("raw","detail","raw_detail","detail_json"):
            if cur.get(key) not in (None,""): queue.append(cur[key])
    return fallback


def analysis_scope_for(content_type: str, has_body: bool) -> str:
    content_type=normalize_content_type(content_type)
    if content_type == "video":
        return "video_description_and_metadata" if has_body else "video_metadata_only"
    return "copy_and_metadata" if has_body else "metadata_only"


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS golden_candidates (
          candidate_id TEXT PRIMARY KEY,
          note_id TEXT DEFAULT '', url TEXT DEFAULT '', title TEXT DEFAULT '', body TEXT DEFAULT '',
          author TEXT DEFAULT '', keyword TEXT DEFAULT '', published_at TEXT DEFAULT '', cover_url TEXT DEFAULT '',
          content_type TEXT DEFAULT 'unknown', analysis_scope TEXT DEFAULT 'metadata_only',
          likes INTEGER DEFAULT 0, collects INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
          source_type TEXT NOT NULL, source_reason_json TEXT DEFAULT '{}', candidate_score REAL DEFAULT 0,
          selected_at TEXT NOT NULL, status TEXT DEFAULT 'pending', raw_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_gc_note ON golden_candidates(note_id);
        CREATE INDEX IF NOT EXISTS idx_gc_url ON golden_candidates(url);
        CREATE TABLE IF NOT EXISTS golden_features (
          candidate_id TEXT PRIMARY KEY, feature_json TEXT NOT NULL, model TEXT DEFAULT '', extracted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS golden_samples (
          golden_id TEXT PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL, note_id TEXT DEFAULT '',
          admission_status TEXT DEFAULT 'PASS', admission_reason_json TEXT DEFAULT '[]',
          golden_score REAL DEFAULT 0, transfer_score REAL DEFAULT 0.5,
          transfer_json TEXT DEFAULT '{}',
          active INTEGER DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS golden_patterns (
          pattern_id TEXT PRIMARY KEY, segment_key TEXT NOT NULL, version INTEGER DEFAULT 1,
          sample_count INTEGER DEFAULT 0, effective_weight REAL DEFAULT 0,
          pattern_json TEXT NOT NULL, evidence_json TEXT DEFAULT '[]', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS golden_usage (
          usage_id TEXT PRIMARY KEY, output_id TEXT DEFAULT '', topic_id TEXT DEFAULT '',
          golden_ids_json TEXT DEFAULT '[]', pattern_ids_json TEXT DEFAULT '[]',
          used_features_json TEXT DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS golden_feedback (
          usage_id TEXT PRIMARY KEY, views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
          favorites INTEGER DEFAULT 0, comments INTEGER DEFAULT 0, performance_score REAL DEFAULT 0,
          observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS golden_heat_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          golden_id TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          likes INTEGER DEFAULT 0, collects INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
          is_baseline INTEGER DEFAULT 0,
          UNIQUE(golden_id, observed_at)
        );
        CREATE INDEX IF NOT EXISTS idx_ghs_golden ON golden_heat_snapshots(golden_id, observed_at);
        """
    )
    # CREATE TABLE IF NOT EXISTS不会给0813已有数据库补列，因此在连接时幂等迁移。
    cols={r[1] for r in con.execute("PRAGMA table_info(golden_candidates)").fetchall()}
    if "content_type" not in cols:
        con.execute("ALTER TABLE golden_candidates ADD COLUMN content_type TEXT DEFAULT 'unknown'")
    if "analysis_scope" not in cols:
        con.execute("ALTER TABLE golden_candidates ADD COLUMN analysis_scope TEXT DEFAULT 'metadata_only'")
    sample_cols={r[1] for r in con.execute("PRAGMA table_info(golden_samples)").fetchall()}
    if "transfer_json" not in sample_cols:
        con.execute("ALTER TABLE golden_samples ADD COLUMN transfer_json TEXT DEFAULT '{}'")
    if "revalidation_score" not in sample_cols:
        con.execute("ALTER TABLE golden_samples ADD COLUMN revalidation_score REAL DEFAULT 1.0")
    if "last_revalidated_at" not in sample_cols:
        con.execute("ALTER TABLE golden_samples ADD COLUMN last_revalidated_at TEXT DEFAULT ''")
    feedback_cols={r[1] for r in con.execute("PRAGMA table_info(golden_feedback)").fetchall()}
    for name,spec in {
        "shares":"INTEGER DEFAULT 0", "rise_fans":"INTEGER DEFAULT 0",
        "avg_view_time_seconds":"REAL", "ctr":"REAL", "completion_rate":"REAL",
        "top_source":"TEXT DEFAULT ''", "dimension_scores_json":"TEXT DEFAULT '{}'",
    }.items():
        if name not in feedback_cols:
            con.execute(f"ALTER TABLE golden_feedback ADD COLUMN {name} {spec}")
    con.execute("UPDATE golden_candidates SET content_type='unknown' WHERE COALESCE(content_type,'')='' ")
    con.execute("""UPDATE golden_candidates SET analysis_scope=CASE
      WHEN content_type='video' AND COALESCE(body,'')!='' THEN 'video_description_and_metadata'
      WHEN content_type='video' THEN 'video_metadata_only'
      WHEN COALESCE(body,'')!='' THEN 'copy_and_metadata'
      ELSE 'metadata_only' END WHERE COALESCE(analysis_scope,'')='' OR analysis_scope='metadata_only'""")
    con.commit()
    return con


def _j(v, default):
    if v in (None, ""):
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


def candidate_from_row(r: sqlite3.Row | dict) -> dict:
    d = dict(r)
    d["content_type"] = normalize_content_type(d.get("content_type"))
    d["analysis_scope"] = d.get("analysis_scope") or analysis_scope_for(d["content_type"],bool(d.get("body")))
    d["source_reason"] = _j(d.pop("source_reason_json", "{}"), {})
    d["raw"] = _j(d.pop("raw_json", "{}"), {})
    return d


def upsert_candidate(note: dict, source_type: str, reason, candidate_score: float = 0.0) -> dict:
    con = connect()
    note_id = str(note.get("note_id") or "")
    url = str(note.get("url") or "")
    existing = None
    if note_id:
        existing = con.execute("SELECT * FROM golden_candidates WHERE note_id=? ORDER BY selected_at DESC LIMIT 1", (note_id,)).fetchone()
    if existing is None and url:
        existing = con.execute("SELECT * FROM golden_candidates WHERE url=? ORDER BY selected_at DESC LIMIT 1", (url,)).fetchone()
    candidate_id = existing["candidate_id"] if existing else "gc_" + uuid.uuid4().hex[:12]
    # Preserve dual provenance: the same post may be independently selected by Human and Agent.
    if existing:
        prior_type=str(existing["source_type"] or "")
        types=[x for x in prior_type.split("+") if x]
        if source_type not in types: types.append(source_type)
        merged_type="+".join(types)
        prior_reason=_j(existing["source_reason_json"],{})
        selections=list(prior_reason.get("selections",[])) if isinstance(prior_reason,dict) else []
    else:
        merged_type=source_type; selections=[]
    selection_time=now()
    item={"type":source_type,"detail":reason,"selected_at":selection_time}
    if json.dumps(item,ensure_ascii=False,sort_keys=True) not in {json.dumps(x,ensure_ascii=False,sort_keys=True) for x in selections}:
        selections.append(item)
    merged_reason={"selections":selections}
    content_type=infer_content_type(note,note.get("content_type") or (existing["content_type"] if existing and "content_type" in existing.keys() else "unknown"))
    analysis_scope=analysis_scope_for(content_type,bool(note.get("body")))
    con.execute(
        """INSERT INTO golden_candidates(
          candidate_id,note_id,url,title,body,author,keyword,published_at,cover_url,content_type,analysis_scope,likes,collects,comments,
          source_type,source_reason_json,candidate_score,selected_at,status,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(candidate_id) DO UPDATE SET
          note_id=excluded.note_id,url=excluded.url,title=excluded.title,body=excluded.body,author=excluded.author,
          keyword=excluded.keyword,published_at=excluded.published_at,cover_url=excluded.cover_url,
          content_type=CASE WHEN excluded.content_type!='unknown' THEN excluded.content_type ELSE golden_candidates.content_type END,
          analysis_scope=CASE WHEN excluded.content_type!='unknown' OR COALESCE(excluded.body,'')!='' THEN excluded.analysis_scope ELSE golden_candidates.analysis_scope END,
          likes=excluded.likes,collects=excluded.collects,comments=excluded.comments,
          source_type=excluded.source_type,source_reason_json=excluded.source_reason_json,
          candidate_score=MAX(golden_candidates.candidate_score,excluded.candidate_score),raw_json=excluded.raw_json,
          selected_at=excluded.selected_at,
          status=CASE WHEN golden_candidates.status='golden' THEN 'golden' ELSE 'pending' END
        """,
        (candidate_id,note_id,url,note.get("title", ""),note.get("body", ""),note.get("author", ""),
         note.get("keyword", ""),note.get("published_at", ""),note.get("cover_url", ""),content_type,analysis_scope,
         int(note.get("likes") or note.get("likes_num") or 0), int(note.get("collects") or note.get("collects_num") or 0),
         int(note.get("comments") or note.get("comments_num") or 0), merged_type,
         json.dumps(merged_reason, ensure_ascii=False), float(candidate_score or 0), selection_time, "pending",
         json.dumps(note.get("raw") or note, ensure_ascii=False))
    )
    con.commit()
    row = con.execute("SELECT * FROM golden_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    con.close()
    return candidate_from_row(row)



def update_candidate_detail(candidate_id: str, detail: dict) -> dict | None:
    con=connect(); row=con.execute("SELECT * FROM golden_candidates WHERE candidate_id=?",(candidate_id,)).fetchone()
    if not row: con.close(); return None
    old=candidate_from_row(row); raw=old.get("raw",{}); raw["detail"]=detail.get("raw_detail", detail)
    content_type=infer_content_type(detail,old.get("content_type","unknown"))
    body=str(detail.get("body") or old.get("body") or "")
    analysis_scope=analysis_scope_for(content_type,bool(body.strip()))
    con.execute("""UPDATE golden_candidates SET
      note_id=CASE WHEN ?!='' THEN ? ELSE note_id END,
      body=CASE WHEN ?!='' THEN ? ELSE body END,
      cover_url=CASE WHEN ?!='' THEN ? ELSE cover_url END,
      likes=CASE WHEN ?>0 THEN ? ELSE likes END,
      collects=CASE WHEN ?>0 THEN ? ELSE collects END,
      comments=CASE WHEN ?>0 THEN ? ELSE comments END,
      content_type=?,analysis_scope=?,raw_json=? WHERE candidate_id=?""",
      (str(detail.get("note_id") or ""),str(detail.get("note_id") or ""),str(detail.get("body") or ""),str(detail.get("body") or ""),
       str(detail.get("cover_url") or ""),str(detail.get("cover_url") or ""),int(detail.get("likes") or 0),int(detail.get("likes") or 0),
       int(detail.get("collects") or 0),int(detail.get("collects") or 0),int(detail.get("comments") or 0),int(detail.get("comments") or 0),
       content_type,analysis_scope,json.dumps(raw,ensure_ascii=False),candidate_id))
    con.commit(); r=con.execute("SELECT * FROM golden_candidates WHERE candidate_id=?",(candidate_id,)).fetchone(); con.close(); return candidate_from_row(r)

def pending_candidates(limit: int = 50) -> list[dict]:
    con = connect()
    rows = con.execute("SELECT * FROM golden_candidates WHERE status IN ('pending','featured') ORDER BY candidate_score DESC, selected_at DESC LIMIT ?", (int(limit),)).fetchall()
    con.close()
    return [candidate_from_row(r) for r in rows]


def list_candidates(limit: int = 100) -> list[dict]:
    """Return every candidate for UI inspection, including admitted/rejected ones."""
    con = connect()
    rows = con.execute(
        """SELECT gc.*, CASE WHEN gf.candidate_id IS NULL THEN 0 ELSE 1 END AS has_feature
           FROM golden_candidates gc
           LEFT JOIN golden_features gf ON gf.candidate_id=gc.candidate_id
           ORDER BY gc.selected_at DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    con.close()
    return [candidate_from_row(r) for r in rows]


def save_feature(candidate_id: str, feature: dict, model: str = "") -> None:
    con = connect()
    con.execute("INSERT OR REPLACE INTO golden_features(candidate_id,feature_json,model,extracted_at) VALUES(?,?,?,?)",
                (candidate_id, json.dumps(feature, ensure_ascii=False), model, now()))
    content_type=normalize_content_type(feature.get("content_type"))
    scope=str(feature.get("analysis_scope") or analysis_scope_for(content_type,False))
    con.execute("""UPDATE golden_candidates SET status=CASE WHEN status='golden' THEN status ELSE 'featured' END,
                content_type=CASE WHEN ?!='unknown' THEN ? ELSE content_type END,analysis_scope=? WHERE candidate_id=?""",
                (content_type,content_type,scope,candidate_id))
    con.commit(); con.close()


def get_feature(candidate_id: str) -> dict | None:
    con = connect(); r = con.execute("SELECT feature_json FROM golden_features WHERE candidate_id=?", (candidate_id,)).fetchone(); con.close()
    return _j(r[0], {}) if r else None


def classify_unknown_media(candidate_id: str, content_type: str) -> dict:
    """允许用户给0813旧unknown样本补标；已明确的媒介不在这里反复改写。"""
    content_type=normalize_content_type(content_type)
    if content_type not in ("image_text","video"):
        raise ValueError("content_type只能是image_text或video")
    con=connect(); row=con.execute("SELECT * FROM golden_candidates WHERE candidate_id=?",(candidate_id,)).fetchone()
    if not row:
        con.close(); raise ValueError("未找到Golden Candidate")
    current=normalize_content_type(row["content_type"])
    if current not in ("unknown",content_type):
        con.close(); raise ValueError("该样本已有明确媒介类型；为避免误改Feature，本入口只补标unknown样本")
    scope=analysis_scope_for(content_type,bool((row["body"] or "").strip()))
    feature_row=con.execute("SELECT feature_json FROM golden_features WHERE candidate_id=?",(candidate_id,)).fetchone()
    if feature_row:
        feature=_j(feature_row[0],{})
        feature["content_type"]=content_type; feature["analysis_scope"]=scope
        if content_type=="video":
            media=feature.get("media") if isinstance(feature.get("media"),dict) else {}
            media.update({"video_file_available":False,"keyframes_available":False,"transcript_available":False,
                          "first_3s_hook":"unknown","shot_structure":[],"speech_structure":[],"subtitle_keywords":[]})
            feature["media"]=media
        con.execute("UPDATE golden_features SET feature_json=? WHERE candidate_id=?",
                    (json.dumps(feature,ensure_ascii=False),candidate_id))
    con.execute("UPDATE golden_candidates SET content_type=?,analysis_scope=? WHERE candidate_id=?",
                (content_type,scope,candidate_id))
    con.commit(); updated=con.execute("SELECT * FROM golden_candidates WHERE candidate_id=?",(candidate_id,)).fetchone(); con.close()
    export_pool()
    return candidate_from_row(updated)


def admit(candidate_id: str, status: str, reasons: list, golden_score: float) -> dict | None:
    con = connect()
    c = con.execute("SELECT * FROM golden_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if not c:
        con.close(); return None
    old = con.execute("SELECT * FROM golden_samples WHERE candidate_id=?", (candidate_id,)).fetchone()
    golden_id = old["golden_id"] if old else "gs_" + uuid.uuid4().hex[:12]
    active = 0 if status == "FAIL" else 1
    con.execute(
        """INSERT INTO golden_samples(golden_id,candidate_id,note_id,admission_status,admission_reason_json,golden_score,transfer_score,active,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
        admission_status=excluded.admission_status,admission_reason_json=excluded.admission_reason_json,
        golden_score=excluded.golden_score,active=excluded.active,updated_at=excluded.updated_at""",
        (golden_id,candidate_id,c["note_id"],status,json.dumps(reasons,ensure_ascii=False),float(golden_score),
         float(old["transfer_score"] if old else 0.5),active, old["created_at"] if old else now(), now()))
    con.execute("UPDATE golden_candidates SET status=? WHERE candidate_id=?", ("golden" if active else "rejected", candidate_id))
    # 入池时记录基线热度快照(只写一次,幂等);之后由 revalidate 追加复查快照。
    if active:
        con.execute(
            """INSERT INTO golden_heat_snapshots(golden_id,observed_at,likes,collects,comments,is_baseline)
               SELECT ?,?,?,?,?,1 WHERE NOT EXISTS(SELECT 1 FROM golden_heat_snapshots WHERE golden_id=? AND is_baseline=1)""",
            (golden_id, now(), int(c["likes"] or 0), int(c["collects"] or 0), int(c["comments"] or 0), golden_id))
    con.commit(); con.close(); export_pool()
    return get_sample(golden_id)


def get_sample(golden_id: str) -> dict | None:
    rows = list_samples(active_only=False)
    return next((x for x in rows if x["golden_id"] == golden_id), None)


def list_samples(active_only: bool = True) -> list[dict]:
    con = connect()
    where = "WHERE gs.active=1" if active_only else ""
    rows = con.execute(f"""
      SELECT gs.*,gc.*,gf.feature_json FROM golden_samples gs
      JOIN golden_candidates gc ON gc.candidate_id=gs.candidate_id
      LEFT JOIN golden_features gf ON gf.candidate_id=gs.candidate_id
      {where} ORDER BY gs.golden_score DESC, gs.created_at DESC
    """).fetchall()
    con.close()
    out=[]
    for r in rows:
        d=dict(r)
        d["source_reason"]=_j(d.pop("source_reason_json", "{}"), {})
        d["admission_reasons"]=_j(d.pop("admission_reason_json", "[]"), [])
        d["features"]=_j(d.pop("feature_json", "{}"), {})
        d["raw"]=_j(d.pop("raw_json", "{}"), {})
        d["transfer_dimensions"]=_j(d.pop("transfer_json", "{}"), {})
        out.append(d)
    return out


def deactivate_samples(golden_ids: list[str]) -> int:
    """滚动 Golden Pool:权重衰减到阈值以下的样本置为 inactive(存档保留,不删除)。"""
    if not golden_ids:
        return 0
    ids = list(dict.fromkeys(golden_ids))
    con = connect()
    con.executemany("UPDATE golden_samples SET active=0, updated_at=? WHERE golden_id=?",
                    [(now(), g) for g in ids])
    con.commit(); con.close()
    return len(ids)


def record_heat_snapshot(golden_id: str, likes: int, collects: int, comments: int,
                         is_baseline: bool = False, observed_at: str = "") -> None:
    """Append a supplied heat snapshot without fetching the platform."""
    observed_at = str(observed_at or now()).strip()
    con = connect()
    con.execute(
        "INSERT OR IGNORE INTO golden_heat_snapshots(golden_id,observed_at,likes,collects,comments,is_baseline) VALUES(?,?,?,?,?,?)",
        (golden_id, observed_at, int(likes or 0), int(collects or 0), int(comments or 0), 1 if is_baseline else 0))
    con.commit(); con.close()


def list_heat_snapshots(golden_id: str) -> list[dict]:
    """按 observed_at 升序返回该 golden 的全部热度快照(含基线)。"""
    con = connect()
    rows = con.execute(
        "SELECT observed_at,likes,collects,comments,is_baseline FROM golden_heat_snapshots WHERE golden_id=? ORDER BY observed_at ASC",
        (golden_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def update_revalidation(golden_id: str, factor: float) -> None:
    """落库当前 revalidation_factor + 上次复查时间。"""
    con = connect()
    con.execute("UPDATE golden_samples SET revalidation_score=?, last_revalidated_at=? WHERE golden_id=?",
                (float(factor), now(), golden_id))
    con.commit(); con.close()


def resolve_sample_ref(ref: dict | str) -> dict | None:
    """Resolve a Golden sample by ID, note ID, URL, or exact title."""
    data = ref if isinstance(ref, dict) else {"golden_id": str(ref or "")}
    values = [str(data.get(k) or "").strip() for k in ("golden_id", "note_id", "url", "title")]
    values = [value for value in values if value]
    for sample in list_samples(active_only=False):
        sample_values = {str(sample.get(k) or "").strip() for k in ("golden_id", "note_id", "url", "title")}
        if any(value in sample_values for value in values):
            return sample
    return None


def export_pool() -> list[dict]:
    items=[]
    for s in list_samples(True):
        items.append({
          "golden_id": s["golden_id"], "note_id": s.get("note_id", ""), "url": s.get("url", ""),
          "source": {"type": s.get("source_type"), "reason": s.get("source_reason"), "candidate_score": s.get("candidate_score")},
          "canonical_copy": {"title": s.get("title", ""), "body": s.get("body", "")},
          "media": {"content_type": s.get("content_type","unknown"), "analysis_scope": s.get("analysis_scope","metadata_only")},
          "author": s.get("author", ""), "keyword": s.get("keyword", ""), "published_at": s.get("published_at", ""),
          "metrics": {"likes": s.get("likes",0), "collects": s.get("collects",0), "comments": s.get("comments",0)},
          "features": s.get("features", {}),
          "admission": {"status": s.get("admission_status"), "reasons": s.get("admission_reasons",[])},
          "scores": {"golden": s.get("golden_score",0), "transfer": s.get("transfer_score",0.5),
                     "revalidation":s.get("revalidation_score",1.0),
                     "last_revalidated_at":s.get("last_revalidated_at", ""),
                     "transfer_dimensions":s.get("transfer_dimensions",{})},
          "raw_source_snapshot": s.get("raw", {})
        })
    POOL_JSON.write_text(json.dumps({"version":1,"updated_at":now(),"samples":items},ensure_ascii=False,indent=2),encoding="utf-8")
    return items


def replace_patterns(patterns: list[dict]) -> list[dict]:
    con=connect(); existing={r["segment_key"]:r for r in con.execute("SELECT * FROM golden_patterns").fetchall()}
    saved=[]
    for p in patterns:
        seg=p["segment_key"]; old=existing.get(seg); pid=old["pattern_id"] if old else "gp_"+uuid.uuid4().hex[:10]
        ver=int(old["version"] if old else 0)+1
        con.execute("""INSERT OR REPLACE INTO golden_patterns(pattern_id,segment_key,version,sample_count,effective_weight,pattern_json,evidence_json,updated_at)
        VALUES(?,?,?,?,?,?,?,?)""",(pid,seg,ver,int(p.get("sample_count",0)),float(p.get("effective_weight",0)),json.dumps(p.get("pattern",{}),ensure_ascii=False),json.dumps(p.get("evidence",[]),ensure_ascii=False),now()))
        saved.append({"pattern_id":pid,"segment_key":seg,"version":ver,**p})
    con.commit(); con.close()
    PATTERN_JSON.write_text(json.dumps({"updated_at":now(),"patterns":saved},ensure_ascii=False,indent=2),encoding="utf-8")
    return saved


def list_patterns() -> list[dict]:
    con=connect(); rows=con.execute("SELECT * FROM golden_patterns ORDER BY effective_weight DESC,sample_count DESC").fetchall(); con.close()
    return [{"pattern_id":r["pattern_id"],"segment_key":r["segment_key"],"version":r["version"],"sample_count":r["sample_count"],"effective_weight":r["effective_weight"],"pattern":_j(r["pattern_json"],{}),"evidence":_j(r["evidence_json"],[]),"updated_at":r["updated_at"]} for r in rows]


def record_usage(output_id: str, topic_id: str, golden_ids: list[str], pattern_ids: list[str], used_features: dict | None=None) -> str:
    usage_id="gu_"+uuid.uuid4().hex[:12]; con=connect()
    con.execute("INSERT INTO golden_usage VALUES(?,?,?,?,?,?,?)",(usage_id,output_id,topic_id,json.dumps(golden_ids),json.dumps(pattern_ids),json.dumps(used_features or {},ensure_ascii=False),now()))
    con.commit(); con.close(); return usage_id


def get_usage(usage_id: str) -> dict | None:
    if not usage_id:
        return None
    con=connect(); row=con.execute("SELECT * FROM golden_usage WHERE usage_id=?",(usage_id,)).fetchone(); con.close()
    if not row:
        return None
    data=dict(row)
    data["golden_ids"]=_j(data.pop("golden_ids_json","[]"),[])
    data["pattern_ids"]=_j(data.pop("pattern_ids_json","[]"),[])
    data["used_features"]=_j(data.pop("used_features_json","{}"),{})
    return data


def add_usage_features(usage_id: str, feature_usage: dict) -> dict | None:
    """幂等合并Topic/Content/Copy/Cover/Audience的具体使用证据。"""
    if not usage_id or not isinstance(feature_usage,dict): return get_usage(usage_id)
    current=get_usage(usage_id)
    if not current: return None
    merged=current.get("used_features") if isinstance(current.get("used_features"),dict) else {}
    for key,value in feature_usage.items():
        if isinstance(value,list):
            old=merged.get(key) if isinstance(merged.get(key),list) else []
            normalized=[]
            for item in [*old,*value]:
                marker=json.dumps(item,ensure_ascii=False,sort_keys=True) if isinstance(item,(dict,list)) else str(item)
                if all((json.dumps(x,ensure_ascii=False,sort_keys=True) if isinstance(x,(dict,list)) else str(x)) != marker for x in normalized):
                    normalized.append(item)
            merged[key]=normalized[:30]
        else:
            merged[key]=value
    con=connect(); con.execute("UPDATE golden_usage SET used_features_json=? WHERE usage_id=?",
                               (json.dumps(merged,ensure_ascii=False),usage_id)); con.commit(); con.close()
    return get_usage(usage_id)


def record_feedback(usage_id: str, views: int, likes: int, favorites: int, comments: int,
                    shares: int=0, rise_fans: int=0, avg_view_time_seconds=None,
                    ctr=None, completion_rate=None, top_source: str="") -> dict:
    import math
    views=max(int(views or 0),1); likes=int(likes or 0); favorites=int(favorites or 0); comments=int(comments or 0)
    shares=int(shares or 0); rise_fans=int(rise_fans or 0)
    raw=(likes+2*favorites+3*comments+2*shares)/views
    perf=max(0.0,min(1.0, math.log1p(raw*100)/math.log(21)))
    def clamp(v,default=.5):
        try:return max(0.0,min(1.0,float(v)))
        except:return default
    save_score=clamp((favorites/views)/.08,.5)
    discussion=clamp((comments/views)/.04,.5)
    share_score=clamp((shares/views)/.03,.5) if shares else .5
    follow_score=clamp((rise_fans/views)/.02,.5) if rise_fans else .5
    consumption=clamp(float(completion_rate),.5) if completion_rate not in (None,"") else \
                clamp(float(avg_view_time_seconds)/30,.5) if avg_view_time_seconds not in (None,"") else .5
    cover_score=clamp(ctr,.5) if ctr not in (None,"") else .5
    dimensions={"topic":round(.6*perf+.4*follow_score,4),
                "audience":round(.45*discussion+.35*follow_score+.20*share_score,4),
                "content":round(.45*consumption+.35*save_score+.20*perf,4),
                "copy":round(.4*consumption+.35*perf+.25*discussion,4),
                "cover":round(cover_score,4)}
    con=connect(); u=con.execute("SELECT * FROM golden_usage WHERE usage_id=?",(usage_id,)).fetchone()
    if not u: con.close(); raise ValueError("unknown usage_id")
    con.execute("""INSERT OR REPLACE INTO golden_feedback(
      usage_id,views,likes,favorites,comments,performance_score,observed_at,shares,rise_fans,
      avg_view_time_seconds,ctr,completion_rate,top_source,dimension_scores_json)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (usage_id,views,likes,favorites,comments,perf,now(),shares,rise_fans,avg_view_time_seconds,
       ctr,completion_rate,str(top_source or ""),json.dumps(dimensions,ensure_ascii=False)))
    con.commit(); con.close()
    recompute_transfer_scores(); export_pool()
    return {"usage_id":usage_id,"performance_score":round(perf,4),"dimension_scores":dimensions,
            "updated_golden_ids":_j(u["golden_ids_json"],[])}

def recompute_transfer_scores() -> None:
    """Idempotent transfer learning: each sample score is recomputed from all observed usages.
    Re-running collection with the same metrics therefore does not repeatedly move the score.
    """
    con=connect(); samples=con.execute("SELECT golden_id FROM golden_samples").fetchall()
    usage_rows=con.execute("SELECT usage_id,golden_ids_json,used_features_json FROM golden_usage").fetchall()
    feedback_rows=con.execute("SELECT usage_id,performance_score,dimension_scores_json FROM golden_feedback").fetchall()
    fb={r["usage_id"]:{"overall":float(r["performance_score"] or 0),
                        **_j(r["dimension_scores_json"],{})} for r in feedback_rows}
    by_gid={r["golden_id"]:[] for r in samples}
    by_gid_dim={r["golden_id"]:{k:[] for k in ("topic","audience","content","copy","cover")} for r in samples}
    for u in usage_rows:
        if u["usage_id"] not in fb: continue
        all_gids=[g for g in _j(u["golden_ids_json"],[]) if g in by_gid]
        used=_j(u["used_features_json"],{})
        for gid in all_gids: by_gid[gid].append(fb[u["usage_id"]]["overall"])
        for dim in by_gid_dim[next(iter(by_gid_dim))] if by_gid_dim else ():
            entries=used.get(dim+"_features") if isinstance(used.get(dim+"_features"),list) else []
            if not entries: continue
            cited={str(x.get("golden_id")) for x in entries if isinstance(x,dict) and x.get("golden_id")}
            targets=[g for g in all_gids if g in cited] if cited else []
            for gid in targets: by_gid_dim[gid][dim].append(float(fb[u["usage_id"]].get(dim,.5)))
    for gid,vals in by_gid.items():
        score=(sum(vals)/len(vals)) if vals else .5
        dimensions={k:round(sum(v)/len(v),4) if v else .5 for k,v in by_gid_dim[gid].items()}
        con.execute("UPDATE golden_samples SET transfer_score=?,transfer_json=?,updated_at=? WHERE golden_id=?",
                    (round(score,4),json.dumps(dimensions,ensure_ascii=False),now(),gid))
    con.commit(); con.close()

def bind_usage_output(usage_id: str, output_id: str) -> None:
    con=connect(); con.execute("UPDATE golden_usage SET output_id=? WHERE usage_id=?",(output_id,usage_id)); con.commit(); con.close()
