"""SQLite + FTS5 storage for crawled posts and learned patterns.

Thread-safe via WAL mode + busy timeout. Single database file at
paperfraud_data/crawler.db.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from paperfraud.crawler.base import CrawledPost, LearnedPattern


def _data_dir() -> Path:
    """Resolve paperfraud_data/ relative to the package root."""
    return Path(__file__).resolve().parent.parent.parent / "paperfraud_data"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> Path:
    """Create tables and FTS5 index if they don't exist. Returns db_path."""
    if db_path is None:
        db_path = _data_dir() / "crawler.db"

    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crawled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            dois TEXT NOT NULL DEFAULT '[]',
            pmids TEXT NOT NULL DEFAULT '[]',
            fetched_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS learned_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'blacklist',
            technique TEXT NOT NULL DEFAULT '',
            detection_hint TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'medium',
            reviewed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (post_id) REFERENCES crawled_posts(source_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts
            USING fts5(title, content);
    """)
    conn.commit()
    conn.close()
    return db_path


def upsert_post(db_path: Path, post: CrawledPost) -> bool:
    """Insert or ignore a post by source_id. Returns True if inserted (new)."""
    conn = _connect(db_path)
    cur = conn.execute(
        "SELECT id FROM crawled_posts WHERE source_id = ?",
        (post.source_id,),
    )
    if cur.fetchone():
        conn.close()
        return False

    conn.execute(
        """INSERT INTO crawled_posts
           (source, source_id, title, url, author, date, content, dois, pmids, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post.source,
            post.source_id,
            post.title,
            post.url,
            post.author,
            post.date,
            post.content,
            json.dumps(post.dois),
            json.dumps(post.pmids),
            post.fetched_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return True


def insert_pattern(db_path: Path, pattern: LearnedPattern) -> int:
    """Insert a learned pattern. Returns the new row id."""
    conn = _connect(db_path)
    cur = conn.execute(
        """INSERT INTO learned_patterns
           (post_id, category, technique, detection_hint, severity, reviewed)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            pattern.post_id,
            pattern.category,
            pattern.technique,
            pattern.detection_hint,
            pattern.severity,
            pattern.reviewed,
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_unlearned_posts(db_path: Path, limit: int = 20) -> list[dict]:
    """Return posts that haven't been analyzed by the learner yet."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT * FROM crawled_posts
           WHERE source_id NOT IN (SELECT DISTINCT post_id FROM learned_patterns)
           ORDER BY date DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_patterns(db_path: Path) -> list[dict]:
    """Return unreviewed patterns for the Web UI review desk."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT lp.*, cp.title as post_title, cp.url as post_url, cp.content as post_content
           FROM learned_patterns lp
           LEFT JOIN crawled_posts cp ON lp.post_id = cp.source_id
           WHERE lp.reviewed = 0
           ORDER BY lp.id DESC""",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_pattern(db_path: Path, pattern_id: int) -> bool:
    """Mark a pattern as reviewed=1 (approved)."""
    conn = _connect(db_path)
    conn.execute(
        "UPDATE learned_patterns SET reviewed = 1 WHERE id = ?",
        (pattern_id,),
    )
    conn.commit()
    conn.close()
    return True


def reject_pattern(db_path: Path, pattern_id: int) -> bool:
    """Mark a pattern as reviewed=-1 (rejected)."""
    conn = _connect(db_path)
    conn.execute(
        "UPDATE learned_patterns SET reviewed = -1 WHERE id = ?",
        (pattern_id,),
    )
    conn.commit()
    conn.close()
    return True


def search_posts(db_path: Path, query: str, limit: int = 20) -> list[dict]:
    """Full-text search via FTS5."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT * FROM crawled_posts
           WHERE id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?)
           ORDER BY date DESC
           LIMIT ?""",
        (query, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_by_doi(db_path: Path, doi: str) -> list[dict]:
    """Find posts that mention a specific DOI."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM crawled_posts WHERE dois LIKE ? ORDER BY date DESC LIMIT 10",
        (f"%{doi}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_by_pmid(db_path: Path, pmid: str) -> list[dict]:
    """Find posts that mention a specific PMID."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM crawled_posts WHERE pmids LIKE ? ORDER BY date DESC LIMIT 10",
        (f"%{pmid}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(db_path: Path) -> dict:
    """Return counts for --stats CLI."""
    conn = _connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM crawled_posts").fetchone()[0]
    pubpeer = conn.execute(
        "SELECT COUNT(*) FROM crawled_posts WHERE source='pubpeer'"
    ).fetchone()[0]
    fbs = conn.execute(
        "SELECT COUNT(*) FROM crawled_posts WHERE source='forbetterscience'"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM learned_patterns WHERE reviewed=0"
    ).fetchone()[0]
    conn.close()
    return {
        "total": total,
        "pubpeer": pubpeer,
        "forbetterscience": fbs,
        "pending": pending,
    }
