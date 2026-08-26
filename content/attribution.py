"""内容归因：把本地选题/草稿与小红书草稿、已发布笔记关联起来。

原则：只做确定性归因，不做模糊猜测。
- 生成/推送时保存 title -> topic_id 的本地记录
- 草稿箱验证成功后保存 platform_draft_id
- 后续 creator-notes 采集时，按 note_id 或唯一规范化标题匹配 topic_id
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_title(title: str) -> str:
    """用于确定性标题匹配：NFKC + 小写 + 去空白/常见标点。"""
    text = unicodedata.normalize("NFKC", str(title or "")).strip().lower()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]<>《》—_-]+", "", text)
    return text


def init_db(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS content_records (
            local_id TEXT PRIMARY KEY,
            topic_id TEXT,
            title TEXT,
            normalized_title TEXT,
            local_path TEXT,
            platform_draft_id TEXT,
            platform_note_id TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_records_title "
        "ON content_records(normalized_title)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_records_note "
        "ON content_records(platform_note_id)"
    )
    con.commit()


def register_local_draft(
    con: sqlite3.Connection,
    *,
    local_id: str,
    topic_id: str,
    title: str,
    local_path: str,
    status: str = "local_saved",
) -> None:
    init_db(con)
    ts = now_iso()
    con.execute(
        """
        INSERT INTO content_records (
            local_id, topic_id, title, normalized_title, local_path,
            platform_draft_id, platform_note_id, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(local_id) DO UPDATE SET
            topic_id=excluded.topic_id,
            title=excluded.title,
            normalized_title=excluded.normalized_title,
            local_path=excluded.local_path,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            local_id,
            topic_id or "",
            title or "",
            normalize_title(title),
            local_path or "",
            "",
            "",
            status,
            ts,
            ts,
        ),
    )
    con.commit()


def mark_draft(
    con: sqlite3.Connection,
    local_id: str,
    draft_id: str = "",
    status: str = "draft_confirmed",
) -> None:
    init_db(con)
    con.execute(
        """
        UPDATE content_records
        SET platform_draft_id=?, status=?, updated_at=?
        WHERE local_id=?
        """,
        (draft_id or "", status, now_iso(), local_id),
    )
    con.commit()


def mark_failed(con: sqlite3.Connection, local_id: str) -> None:
    init_db(con)
    con.execute(
        "UPDATE content_records SET status=?, updated_at=? WHERE local_id=?",
        ("platform_failed", now_iso(), local_id),
    )
    con.commit()


def bootstrap_local_drafts(con: sqlite3.Connection, drafts_dir: Path) -> int:
    """把旧 content/drafts/*.json 纳入归因表，便于历史帖子也能匹配。"""
    init_db(con)
    if not drafts_dir.exists():
        return 0
    added = 0
    for path in drafts_dir.glob("*.json"):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        title = str(obj.get("title") or "").strip()
        topic_id = str(obj.get("topic_id") or "").strip()
        if not title or not topic_id:
            continue
        local_id = "legacy:" + path.stem
        exists = con.execute(
            "SELECT 1 FROM content_records WHERE local_id=?", (local_id,)
        ).fetchone()
        if exists:
            continue
        register_local_draft(
            con,
            local_id=local_id,
            topic_id=topic_id,
            title=title,
            local_path=str(path),
            status="legacy_local",
        )
        added += 1
    return added


def resolve_topic(con: sqlite3.Connection, note_id: str, title: str) -> str:
    """按已知 note_id 或唯一标题确定 topic_id；不做模糊匹配。"""
    init_db(con)
    if note_id:
        row = con.execute(
            """
            SELECT topic_id FROM content_records
            WHERE platform_note_id=? AND COALESCE(topic_id,'')!=''
            ORDER BY updated_at DESC LIMIT 1
            """,
            (str(note_id),),
        ).fetchone()
        if row and row[0]:
            return str(row[0])

    norm = normalize_title(title)
    if not norm:
        return ""
    rows = con.execute(
        """
        SELECT DISTINCT topic_id FROM content_records
        WHERE normalized_title=? AND COALESCE(topic_id,'')!=''
        """,
        (norm,),
    ).fetchall()
    topics = {str(r[0]) for r in rows if r and r[0]}
    return next(iter(topics)) if len(topics) == 1 else ""


def mark_published(con: sqlite3.Connection, note_id: str, title: str, topic_id: str) -> None:
    """把唯一匹配的本地内容记录升级为 published，并保存 note_id。"""
    init_db(con)
    norm = normalize_title(title)
    if not note_id or not norm or not topic_id:
        return
    rows = con.execute(
        """
        SELECT local_id FROM content_records
        WHERE normalized_title=? AND topic_id=?
        ORDER BY updated_at DESC
        """,
        (norm, topic_id),
    ).fetchall()
    if not rows:
        return
    # 同题同标题可能有历史重复草稿；只关联最近一条，避免一个 note_id 写到多条记录。
    local_id = rows[0][0]
    con.execute(
        """
        UPDATE content_records
        SET platform_note_id=?, status='published', updated_at=?
        WHERE local_id=?
        """,
        (str(note_id), now_iso(), local_id),
    )
    con.commit()


def reconcile_existing_notes(con: sqlite3.Connection, drafts_dir: Path) -> int:
    """给 note_metrics 中 topic_id 为空的历史记录补归因。返回补上的记录数。"""
    init_db(con)
    bootstrap_local_drafts(con, drafts_dir)
    rows = con.execute(
        """
        SELECT rowid, note_id, title FROM note_metrics
        WHERE COALESCE(topic_id,'')=''
        ORDER BY collected DESC
        """
    ).fetchall()
    mapped = 0
    for rowid, note_id, title in rows:
        topic_id = resolve_topic(con, str(note_id or ""), str(title or ""))
        if not topic_id:
            continue
        con.execute("UPDATE note_metrics SET topic_id=? WHERE rowid=?", (topic_id, rowid))
        mark_published(con, str(note_id or ""), str(title or ""), topic_id)
        mapped += 1
    con.commit()
    return mapped
