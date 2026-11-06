"""Small database layer: PostgreSQL in production, SQLite for local tests."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    engines TEXT,
    priorities TEXT,
    question_count INTEGER,
    note TEXT
);
CREATE TABLE IF NOT EXISTS results (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    checked_at TIMESTAMPTZ NOT NULL,
    question_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT,
    language TEXT, market TEXT, category TEXT, priority TEXT,
    prompt TEXT NOT NULL,
    expected_url TEXT,
    mentioned BOOLEAN, cited BOOLEAN, brand_in_text BOOLEAN,
    rank INTEGER,
    expected_hit BOOLEAN,
    target_urls TEXT, competitors TEXT, cited_domains TEXT, search_queries TEXT,
    answer_text TEXT,
    input_tokens INTEGER, output_tokens INTEGER, search_calls INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS results_run_idx ON results(run_id);
CREATE INDEX IF NOT EXISTS results_q_idx ON results(question_id, engine);
CREATE TABLE IF NOT EXISTS ai_referrals (
    day DATE NOT NULL, source TEXT NOT NULL, path TEXT NOT NULL, hits INTEGER NOT NULL,
    PRIMARY KEY (day, source, path)
);
CREATE TABLE IF NOT EXISTS ai_crawlers (
    day DATE NOT NULL, bot TEXT NOT NULL, hits INTEGER NOT NULL,
    PRIMARY KEY (day, bot)
);
CREATE TABLE IF NOT EXISTS run_requests (
    id SERIAL PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL,
    engines TEXT,
    priorities TEXT,
    limit_n INTEGER,
    status TEXT NOT NULL,
    run_id INTEGER,
    error TEXT
);
"""

SQLITE_SCHEMA = (PG_SCHEMA
                 .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
                 .replace("TIMESTAMPTZ", "TEXT")
                 .replace("BOOLEAN", "INTEGER")
                 .replace("DATE", "TEXT"))

JSON_FIELDS = ("target_urls", "competitors", "cited_domains", "search_queries")


class DB:
    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self.kind = "sqlite"
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        elif url.startswith(("postgres://", "postgresql://")):
            import psycopg
            from psycopg.rows import dict_row
            self.kind = "pg"
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            raise ValueError(f"不支持的 DATABASE_URL: {url}")

    def _sql(self, sql: str) -> str:
        return sql.replace("%s", "?") if self.kind == "sqlite" else sql

    def execute(self, sql: str, params: tuple | list = ()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        if self.kind == "sqlite":
            self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        return [dict(r) for r in cur.fetchall()]

    def init(self) -> None:
        schema = PG_SCHEMA if self.kind == "pg" else SQLITE_SCHEMA
        for stmt in schema.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def insert(self, table: str, row: dict) -> int:
        cols = list(row)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in row.values()]
        if self.kind == "pg":
            return self.execute(sql + " RETURNING id", vals).fetchone()["id"]
        return self.execute(sql, vals).lastrowid

    def upsert_max(self, table: str, keys: dict, hits: int) -> None:
        """Insert a count, or keep the larger of old/new. Log files for a day
        only grow until they are rotated away, so the max is the best count."""
        cols = list(keys) + ["hits"]
        conflict = ", ".join(keys)
        greater = "GREATEST" if self.kind == "pg" else "MAX"
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
               f"ON CONFLICT ({conflict}) DO UPDATE SET hits = {greater}({table}.hits, excluded.hits)")
        self.execute(sql, list(keys.values()) + [hits])

    def close(self) -> None:
        self.conn.close()


def decode_result(row: dict) -> dict:
    for f in JSON_FIELDS:
        v = row.get(f)
        row[f] = json.loads(v) if isinstance(v, str) and v else (v or [])
    for f in ("mentioned", "cited", "brand_in_text", "expected_hit"):
        if row.get(f) is not None:
            row[f] = bool(row[f])
    for f in ("checked_at", "started_at", "finished_at"):
        v = row.get(f)
        if isinstance(v, str) and v:
            row[f] = datetime.fromisoformat(v)
    return row
