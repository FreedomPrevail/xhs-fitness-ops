"""Persistent memory for one-person content operations."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "personal_ops.db"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS sync_events (
          event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL, imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_decisions (
          run_id TEXT PRIMARY KEY, decision_date TEXT NOT NULL,
          selected_topic_id TEXT DEFAULT '', decision_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_daily_decision_date
          ON daily_decisions(decision_date, created_at);
        CREATE TABLE IF NOT EXISTS material_items (
          material_id TEXT PRIMARY KEY, kind TEXT DEFAULT 'note', title TEXT DEFAULT '',
          local_path TEXT DEFAULT '', notes TEXT DEFAULT '', tags_json TEXT DEFAULT '[]',
          rights_status TEXT DEFAULT 'owned', active INTEGER DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    material_columns = {row[1] for row in con.execute("PRAGMA table_info(material_items)")}
    for name, definition in (
        ("content_text", "TEXT DEFAULT ''"),
        ("purpose", "TEXT DEFAULT 'general_reference'"),
        ("content_hash", "TEXT DEFAULT ''"),
    ):
        if name not in material_columns:
            con.execute(f"ALTER TABLE material_items ADD COLUMN {name} {definition}")
    con.commit()
    return con


def record_sync(event_type: str, payload) -> str:
    event_id = "sync_" + uuid.uuid4().hex[:12]
    con = connect()
    con.execute(
        "INSERT INTO sync_events VALUES(?,?,?,?)",
        (event_id, str(event_type), json.dumps(payload, ensure_ascii=False), now()),
    )
    con.commit(); con.close()
    return event_id


def save_decision(decision: dict, run_id: str | None = None) -> str:
    run_id = run_id or "daily_" + uuid.uuid4().hex[:12]
    created = now()
    con = connect()
    con.execute(
        "INSERT OR REPLACE INTO daily_decisions VALUES(?,?,?,?,?)",
        (run_id, created[:10], str(decision.get("selected_topic_id") or ""),
         json.dumps(decision, ensure_ascii=False), created),
    )
    con.commit(); con.close()
    return run_id


def recent_decisions(limit: int = 14) -> list[dict]:
    con = connect()
    rows = con.execute(
        "SELECT * FROM daily_decisions ORDER BY created_at DESC LIMIT ?", (int(limit),)
    ).fetchall()
    con.close()
    out = []
    for row in rows:
        item = dict(row)
        item["decision"] = json.loads(item.pop("decision_json") or "{}")
        out.append(item)
    return out


def upsert_material(item: dict) -> dict:
    material_id = str(item.get("material_id") or "mat_" + uuid.uuid4().hex[:12])
    created = now()
    con = connect()
    old = con.execute("SELECT created_at FROM material_items WHERE material_id=?", (material_id,)).fetchone()
    con.execute(
        """INSERT OR REPLACE INTO material_items(
          material_id,kind,title,local_path,notes,tags_json,rights_status,active,created_at,updated_at,
          content_text,purpose,content_hash
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (material_id, str(item.get("kind") or "note"), str(item.get("title") or ""),
         str(item.get("local_path") or ""), str(item.get("notes") or ""),
         json.dumps(item.get("tags") or [], ensure_ascii=False),
         str(item.get("rights_status") or "owned"), 1 if item.get("active", True) else 0,
         old["created_at"] if old else created, created,
         str(item.get("content_text") or ""),
         str(item.get("purpose") or "general_reference"),
         str(item.get("content_hash") or "")),
    )
    con.commit(); con.close()
    return {"material_id": material_id, **item}


def list_materials(limit: int = 100) -> list[dict]:
    con = connect()
    rows = con.execute(
        "SELECT * FROM material_items WHERE active=1 ORDER BY updated_at DESC LIMIT ?", (int(limit),)
    ).fetchall()
    con.close()
    out = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        out.append(item)
    return out


def writing_memory(materials: list[dict] | None = None, *, selected_ids: list[str] | None = None,
                   limit: int = 6, excerpt_chars: int = 1800) -> list[dict]:
    """Return owned writing samples as bounded voice evidence, never as factual evidence."""
    rows = list(materials if materials is not None else list_materials(100))
    selected = [str(item) for item in (selected_ids or []) if item]
    selected_rank = {material_id: index for index, material_id in enumerate(selected)}
    allowed_rights = {"owned", "authorized"}
    rows = [row for row in rows
            if row.get("kind") in {"writing_sample", "voice_sample"}
            and str(row.get("rights_status") or "") in allowed_rights
            and str(row.get("content_text") or "").strip()]
    rows.sort(key=lambda row: (selected_rank.get(str(row.get("material_id")), len(selected_rank) + 1),
                               str(row.get("updated_at") or "")), reverse=False)
    return [{
        "material_id": row.get("material_id"),
        "title": row.get("title", ""),
        "purpose": row.get("purpose") or "voice_and_argumentation_style_only",
        "rights_status": row.get("rights_status"),
        "tags": row.get("tags", []),
        "excerpt": str(row.get("content_text") or "")[:max(200, int(excerpt_chars))],
    } for row in rows[:max(1, int(limit))]]
