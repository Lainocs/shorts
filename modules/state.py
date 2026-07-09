import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    fact_summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    youtube_video_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def get_recent_topics(db_path: str, limit: int = 50) -> list[dict]:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT title, fact_summary FROM topics ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def add_topic(db_path: str, title: str, fact_summary: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO topics (title, fact_summary, created_at) VALUES (?, ?, ?)",
            (title, fact_summary, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def add_video(db_path: str, topic_id: int, youtube_video_id: str | None, status: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO videos (topic_id, youtube_video_id, status, created_at) VALUES (?, ?, ?, ?)",
            (topic_id, youtube_video_id, status, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
