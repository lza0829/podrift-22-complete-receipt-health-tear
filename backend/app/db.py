from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def db_immediate():
    """写锁前置的事务：用于“读探针灯→判定完成→落库”必须原子的场景。

    并发下两笔启停互相翻转时，先拿写锁者完成，后到者在其后的灯色上判定，
    避免读到半截状态。BEGIN IMMEDIATE 需要连接处于自动提交模式。
    """
    conn = connect()
    conn.isolation_level = None
    began = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        began = True
        yield conn
        conn.execute("COMMIT")
    except Exception:
        if began:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          tag TEXT NOT NULL,
          pull_state TEXT NOT NULL,
          layers_done INTEGER NOT NULL DEFAULT 0,
          layers_total INTEGER NOT NULL DEFAULT 4,
          ready_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pods (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          image_id INTEGER,
          run_state TEXT NOT NULL,
          sidecar TEXT,
          cpu_quota REAL NOT NULL DEFAULT 1.0,
          mem_quota_mb INTEGER NOT NULL DEFAULT 512,
          FOREIGN KEY(image_id) REFERENCES images(id)
        );
        CREATE TABLE IF NOT EXISTS start_orders (
          id INTEGER PRIMARY KEY,
          pod_id INTEGER NOT NULL,
          action TEXT NOT NULL,
          target_image_id INTEGER,
          status TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          -- 完成定义：先成并挂缺口账。以下四列在“完成”那一刻定格，之后不可改写，
          -- 因此即使健康灯事后翻转，完成结局仍可重放。
          probe_color_at_complete TEXT,
          gap INTEGER NOT NULL DEFAULT 0,
          gap_reason TEXT NOT NULL DEFAULT '',
          completed_at TEXT,
          FOREIGN KEY(pod_id) REFERENCES pods(id)
        );
        CREATE TABLE IF NOT EXISTS probes (
          id INTEGER PRIMARY KEY,
          pod_id INTEGER NOT NULL UNIQUE,
          color TEXT NOT NULL,
          fail_streak INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT,
          FOREIGN KEY(pod_id) REFERENCES pods(id)
        );
        CREATE TABLE IF NOT EXISTS quotas (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          cpu_used REAL NOT NULL,
          cpu_limit REAL NOT NULL,
          mem_used_mb INTEGER NOT NULL,
          mem_limit_mb INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS restart_policy (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          mode TEXT NOT NULL,
          backoff_sec INTEGER NOT NULL DEFAULT 5
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY,
          kind TEXT NOT NULL,
          ref TEXT,
          detail TEXT NOT NULL,
          at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    # 存量库迁移：CREATE TABLE IF NOT EXISTS 不会给旧表补列，逐列幂等补齐。
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(start_orders)").fetchall()}
    migrations = [
        ("probe_color_at_complete", "TEXT"),
        ("gap", "INTEGER NOT NULL DEFAULT 0"),
        ("gap_reason", "TEXT NOT NULL DEFAULT ''"),
        ("completed_at", "TEXT"),
    ]
    for col, ddl in migrations:
        if col not in existing:
            conn.execute(f"ALTER TABLE start_orders ADD COLUMN {col} {ddl}")
    conn.commit()
