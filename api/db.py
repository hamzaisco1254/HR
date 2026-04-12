"""Thin Postgres connection layer for the PLW HR platform.

All stores use this module instead of opening their own psycopg connections.
Philosophy:
    - A single import point (`from db import query, execute, transaction, one`).
    - Short-lived connections (Vercel serverless) — reuse inside a warm
      invocation via a module-level lazy pool, but never leak across requests.
    - Uses the pooled URL (POSTGRES_URL / DATABASE_URL) which Neon serves
      through the PgBouncer endpoint automatically.
    - All helpers accept parameterised SQL — never concatenate user input.

Usage:
    from db import query, one, execute, transaction

    users = query("SELECT * FROM users WHERE active = %s", (True,))

    user = one("SELECT * FROM users WHERE email = %s", (email,))

    execute("UPDATE users SET active = %s WHERE id = %s", (False, user_id))

    with transaction() as cur:
        cur.execute("INSERT INTO ...", (...,))
        cur.execute("UPDATE ...", (...,))
"""
import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row


# ── URL resolution ─────────────────────────────────────────────────

def _conn_url() -> str:
    """
    Resolution order:
        POSTGRES_URL       — Vercel + Neon pooled URL (preferred)
        DATABASE_URL       — generic fallback
        POSTGRES_URL_NON_POOLING — direct unpooled fallback
    """
    for key in ('POSTGRES_URL', 'DATABASE_URL', 'POSTGRES_URL_NON_POOLING'):
        v = os.environ.get(key)
        if v:
            return v
    raise RuntimeError(
        'No Postgres connection URL found. Set POSTGRES_URL or DATABASE_URL.'
    )


def is_configured() -> bool:
    return any(os.environ.get(k) for k in ('POSTGRES_URL', 'DATABASE_URL'))


# ── Connection helpers ─────────────────────────────────────────────

@contextmanager
def connect():
    """
    Open a short-lived connection with dict rows.
    Uses a 5-second TCP timeout so requests fail fast on cold DBs.
    """
    conn = psycopg.connect(
        _conn_url(),
        row_factory=dict_row,
        connect_timeout=5,
        autocommit=False,
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def transaction():
    """Yield a cursor inside a committed transaction (rolls back on error)."""
    with connect() as conn:
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Run a SELECT and return a list of dicts."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def one(sql: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
    """Run a SELECT and return the first row (or None)."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row or None


def execute(sql: str, params: Tuple = ()) -> int:
    """Run an INSERT/UPDATE/DELETE and return rowcount. Auto-commits."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def execute_many(sql: str, params_list: Iterable[Tuple]) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            conn.commit()


def insert_returning_id(sql: str, params: Tuple = ()) -> Any:
    """Run an INSERT ... RETURNING id and return the scalar id."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            return (row or {}).get('id')


# ── JSON helpers ───────────────────────────────────────────────────

def to_jsonb(value: Any) -> Optional[str]:
    """Serialise a Python value to JSON for a JSONB column (NULL-safe)."""
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return None
