"""SQLite logger nhẹ cho 20 camera."""

from __future__ import annotations

import os
import sqlite3
import time
import json
from datetime import datetime
from typing import Dict, List


class DatabaseManager:
    def __init__(self, db_file: str, log_interval_sec: int = 5, retention_days: int = 7, max_rows_per_camera: int = 20000) -> None:
        self.db_file = db_file
        self.log_interval_sec = log_interval_sec
        self.retention_days = retention_days
        self.max_rows_per_camera = max_rows_per_camera
        self.last_log: Dict[str, float] = {}
        self.last_cleanup = 0.0
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS water_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    camera_name TEXT,
                    ts TEXT NOT NULL,
                    level REAL NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_water_cam_ts ON water_history(camera_id, ts)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT,
                    message TEXT NOT NULL,
                    severity TEXT DEFAULT 'INFO',
                    username TEXT,
                    meta TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_ts ON event_history(ts)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_tasks(
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    camera_id TEXT,
                    status TEXT DEFAULT 'open',
                    context TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_tasks_updated ON chat_tasks(updated_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    role TEXT NOT NULL,
                    message TEXT NOT NULL,
                    engine TEXT,
                    meta TEXT,
                    FOREIGN KEY(task_id) REFERENCES chat_tasks(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_task ON chat_messages(task_id, id)")

    def log(self, camera_id: str, camera_name: str, level: float, status: str, confidence: float) -> None:
        now = time.time()
        if now - self.last_log.get(camera_id, 0) < self.log_interval_sec:
            return
        self.last_log[camera_id] = now
        with sqlite3.connect(self.db_file) as conn:
            conn.execute(
                "INSERT INTO water_history(camera_id,camera_name,ts,level,status,confidence) VALUES(?,?,?,?,?,?)",
                (camera_id, camera_name, datetime.now().isoformat(timespec="seconds"), level, status, confidence),
            )
        if now - self.last_cleanup > 3600:
            self.cleanup()
            self.last_cleanup = now

    def cleanup(self) -> None:
        cutoff = datetime.fromtimestamp(time.time() - self.retention_days * 86400).isoformat(timespec="seconds")
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("DELETE FROM water_history WHERE ts < ?", (cutoff,))
            conn.execute("DELETE FROM event_history WHERE ts < ?", (cutoff,))
            cams = [r[0] for r in conn.execute("SELECT DISTINCT camera_id FROM water_history").fetchall()]
            for cam in cams:
                conn.execute(
                    "DELETE FROM water_history WHERE camera_id=? AND id NOT IN (SELECT id FROM water_history WHERE camera_id=? ORDER BY id DESC LIMIT ?)",
                    (cam, cam, self.max_rows_per_camera),
                )


    def log_event(self, event_type: str, source: str = "SYSTEM", message: str = "", severity: str = "INFO", username: str | None = None, meta: str | None = None) -> None:
        with sqlite3.connect(self.db_file) as conn:
            conn.execute(
                "INSERT INTO event_history(ts,event_type,source,message,severity,username,meta) VALUES(?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), event_type, source, message, severity, username, meta),
            )

    def events(self, limit: int = 300, event_type: str | None = None) -> List[Dict]:
        limit = max(1, min(int(limit), 2000))
        with sqlite3.connect(self.db_file) as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT ts,event_type,source,message,severity,username,meta FROM event_history WHERE event_type=? ORDER BY id DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ts,event_type,source,message,severity,username,meta FROM event_history ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        rows.reverse()
        return [
            {"ts": r[0], "event_type": r[1], "source": r[2], "message": r[3], "severity": r[4], "username": r[5], "meta": r[6]}
            for r in rows
        ]

    def history(self, camera_id: str | None = None, limit: int = 300) -> List[Dict]:
        limit = max(1, min(int(limit), 2000))
        with sqlite3.connect(self.db_file) as conn:
            if camera_id:
                rows = conn.execute(
                    "SELECT camera_id,camera_name,ts,level,status,confidence FROM water_history WHERE camera_id=? ORDER BY id DESC LIMIT ?",
                    (camera_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT camera_id,camera_name,ts,level,status,confidence FROM water_history ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        rows.reverse()
        return [
            {"camera_id": r[0], "camera_name": r[1], "ts": r[2], "level": r[3], "status": r[4], "confidence": r[5]}
            for r in rows
        ]

    def create_chat_task(self, task_id: str, title: str, camera_id: str | None = None, context: Dict | None = None) -> Dict:
        now = datetime.now().isoformat(timespec="seconds")
        ctx = json.dumps(context or {}, ensure_ascii=False)
        with sqlite3.connect(self.db_file) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chat_tasks(id,title,created_at,updated_at,camera_id,status,context) VALUES(?,?,?,?,?,?,?)",
                (task_id, title, now, now, camera_id, "open", ctx),
            )
        return {"id": task_id, "title": title, "created_at": now, "updated_at": now, "camera_id": camera_id, "status": "open", "context": context or {}}

    def upsert_chat_task(self, task_id: str, title: str | None = None, camera_id: str | None = None, context: Dict | None = None, status: str = "open") -> None:
        now = datetime.now().isoformat(timespec="seconds")
        ctx = json.dumps(context or {}, ensure_ascii=False)
        with sqlite3.connect(self.db_file) as conn:
            row = conn.execute("SELECT title,created_at FROM chat_tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE chat_tasks SET title=?, updated_at=?, camera_id=COALESCE(?, camera_id), status=?, context=? WHERE id=?",
                    (title or row[0], now, camera_id, status, ctx, task_id),
                )
            else:
                conn.execute(
                    "INSERT INTO chat_tasks(id,title,created_at,updated_at,camera_id,status,context) VALUES(?,?,?,?,?,?,?)",
                    (task_id, title or "WaterAI chat task", now, now, camera_id, status, ctx),
                )

    def log_chat_message(self, task_id: str, role: str, message: str, engine: str | None = None, meta: Dict | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_file) as conn:
            conn.execute(
                "INSERT INTO chat_messages(task_id,ts,role,message,engine,meta) VALUES(?,?,?,?,?,?)",
                (task_id, now, role, message, engine, json.dumps(meta or {}, ensure_ascii=False)),
            )
            conn.execute("UPDATE chat_tasks SET updated_at=? WHERE id=?", (now, task_id))

    def chat_tasks(self, limit: int = 50) -> List[Dict]:
        limit = max(1, min(int(limit), 200))
        with sqlite3.connect(self.db_file) as conn:
            rows = conn.execute(
                "SELECT id,title,created_at,updated_at,camera_id,status,context FROM chat_tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "title": r[1],
                "created_at": r[2],
                "updated_at": r[3],
                "camera_id": r[4],
                "status": r[5],
                "context": json.loads(r[6] or "{}"),
            }
            for r in rows
        ]

    def chat_task(self, task_id: str, limit: int = 200) -> Dict | None:
        limit = max(1, min(int(limit), 500))
        with sqlite3.connect(self.db_file) as conn:
            task = conn.execute(
                "SELECT id,title,created_at,updated_at,camera_id,status,context FROM chat_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not task:
                return None
            rows = conn.execute(
                "SELECT ts,role,message,engine,meta FROM chat_messages WHERE task_id=? ORDER BY id ASC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return {
            "id": task[0],
            "title": task[1],
            "created_at": task[2],
            "updated_at": task[3],
            "camera_id": task[4],
            "status": task[5],
            "context": json.loads(task[6] or "{}"),
            "messages": [
                {"ts": r[0], "role": r[1], "message": r[2], "engine": r[3], "meta": json.loads(r[4] or "{}")}
                for r in rows
            ],
        }

    def delete_chat_task(self, task_id: str) -> bool:
        with sqlite3.connect(self.db_file) as conn:
            cur = conn.execute("DELETE FROM chat_messages WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM chat_tasks WHERE id=?", (task_id,))
            return cur.rowcount >= 0
