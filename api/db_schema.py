"""Database schema bootstrap for the PLW HR platform.

Idempotent — safe to run multiple times. Creates all tables and indexes
if they do not yet exist, and seeds the admin user from env vars.

Can be invoked:
    1. Standalone:     `python api/db_schema.py`
    2. Programmatic:   `from db_schema import bootstrap; bootstrap()`
    3. Via admin API:  POST /api/admin/bootstrap  (admin-only)

Schema overview:

    users           user accounts (replaces hr_users.json)
    audit_log       append-only security audit trail
    invoices        supplier invoices (replaces hr_invoices.json)
    payments        bank-account payment records
    balances        per-account balance snapshots
    persons         trip participants
    dossiers        trip / visa dossiers per person
    dossier_docs    checklist items per dossier
    rag_documents   uploaded knowledge-base files
    rag_chunks      text chunks + embeddings (JSONB until pgvector)
    doc_history     generated attestations / ordres de mission
    chat_messages   Mr Hamza conversation memory
    kv_settings     generic key/value for company config, references, etc.
"""
import os
import uuid
from datetime import datetime

from werkzeug.security import generate_password_hash

import db


SCHEMA_SQL = r"""
-- ── Extensions ──────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Users & auth ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'user',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS users_email_idx ON users (LOWER(email));

-- ── Audit log ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    action          TEXT NOT NULL,
    actor_id        TEXT,
    actor_email     TEXT,
    entity_type     TEXT,
    entity_id       TEXT,
    ip              TEXT,
    ok              BOOLEAN NOT NULL DEFAULT TRUE,
    details         JSONB
);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx     ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_action_idx ON audit_log (action);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx  ON audit_log (actor_id);

-- ── Invoices ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,
    supplier_name   TEXT NOT NULL DEFAULT '',
    invoice_ref     TEXT,
    invoice_date    DATE,
    due_date        DATE,
    amount          NUMERIC(15, 2) NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'TND',
    payment_status  TEXT NOT NULL DEFAULT 'unpaid',
    notes           TEXT,
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS invoices_status_idx   ON invoices (payment_status);
CREATE INDEX IF NOT EXISTS invoices_supplier_idx ON invoices (supplier_name);
CREATE INDEX IF NOT EXISTS invoices_date_idx     ON invoices (invoice_date DESC);

-- ── Payments (bank movements) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,
    account_name    TEXT NOT NULL,
    amount          NUMERIC(15, 2) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'TND',
    direction       TEXT NOT NULL DEFAULT 'out',  -- 'in' | 'out'
    description     TEXT,
    payment_date    DATE NOT NULL,
    reference       TEXT,
    invoice_id      TEXT REFERENCES invoices(id) ON DELETE SET NULL,
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payments_date_idx    ON payments (payment_date DESC);
CREATE INDEX IF NOT EXISTS payments_account_idx ON payments (account_name);

-- ── Account balances ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS balances (
    id              TEXT PRIMARY KEY,
    account_name    TEXT NOT NULL,
    balance         NUMERIC(15, 2) NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'TND',
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS balances_account_unique ON balances (account_name, currency);

-- ── Trips: persons ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS persons (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    passport_number TEXT,
    nationality     TEXT,
    notes           TEXT,
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Trips: dossiers ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dossiers (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    type            TEXT NOT NULL DEFAULT 'trip',  -- 'trip' | 'visa'
    destination     TEXT,
    purpose         TEXT,
    start_date      DATE,
    end_date        DATE,
    status          TEXT NOT NULL DEFAULT 'draft',
    priority        TEXT NOT NULL DEFAULT 'normal',
    notes           TEXT,
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dossiers_person_idx ON dossiers (person_id);
CREATE INDEX IF NOT EXISTS dossiers_status_idx ON dossiers (status);

-- ── Dossier checklist documents ─────────────────────────────────
CREATE TABLE IF NOT EXISTS dossier_docs (
    id              TEXT PRIMARY KEY,
    dossier_id      TEXT NOT NULL REFERENCES dossiers(id) ON DELETE CASCADE,
    doc_key         TEXT NOT NULL,
    label           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    file_name       TEXT,
    file_size       INTEGER,
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS dossier_docs_unique ON dossier_docs (dossier_id, doc_key);

-- ── RAG knowledge base ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_documents (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'other',
    size            INTEGER NOT NULL DEFAULT 0,
    uploaded_by     TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_documents_category_idx ON rag_documents (category);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id              BIGSERIAL PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    text            TEXT NOT NULL,
    embedding       JSONB NOT NULL   -- float array; future work: migrate to pgvector
);
CREATE INDEX IF NOT EXISTS rag_chunks_doc_idx ON rag_chunks (doc_id);

-- ── Generated HR documents history ──────────────────────────────
CREATE TABLE IF NOT EXISTS doc_history (
    id              TEXT PRIMARY KEY,
    reference       TEXT,
    doc_type        TEXT NOT NULL,
    employee_name   TEXT NOT NULL,
    file_path       TEXT,
    generated_by    TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS doc_history_created_idx ON doc_history (created_at DESC);
CREATE INDEX IF NOT EXISTS doc_history_type_idx    ON doc_history (doc_type);

-- ── Chat message memory ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
    session_id      TEXT,
    role            TEXT NOT NULL,    -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_user_idx ON chat_messages (user_id, created_at DESC);

-- ── Generic key/value store (company config, references counter, etc.)
CREATE TABLE IF NOT EXISTS kv_settings (
    key             TEXT PRIMARY KEY,
    value           JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def bootstrap() -> dict:
    """Create schema (idempotent) and seed admin user from env vars.

    Returns a dict with a short summary so callers can log / display it.
    """
    summary = {'tables_ensured': False, 'admin_seeded': False}

    # 1) Run all DDL statements
    with db.transaction() as cur:
        cur.execute(SCHEMA_SQL)
    summary['tables_ensured'] = True

    # 2) Ensure at least one admin user
    existing = db.one("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    if existing:
        summary['admin_exists'] = True
        return summary

    admin_email = (os.environ.get('ADMIN_EMAIL') or 'admin@example.invalid').strip()
    admin_pass  = (os.environ.get('ADMIN_PASSWORD') or '').strip()
    if not admin_pass:
        import secrets as _s
        admin_pass = _s.token_urlsafe(16)
        summary['generated_password'] = admin_pass
        print(f'[BOOTSTRAP] Generated one-time admin password: {admin_pass}')

    db.execute(
        """INSERT INTO users (id, email, password_hash, name, role, active, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            str(uuid.uuid4()),
            admin_email,
            generate_password_hash(admin_pass),
            'Administrateur',
            'admin',
            True,
            datetime.utcnow(),
        ),
    )
    summary['admin_seeded'] = True
    summary['admin_email']  = admin_email
    return summary


if __name__ == '__main__':
    # Standalone invocation (local dev or one-off runs)
    import json as _json
    result = bootstrap()
    print(_json.dumps(result, indent=2, default=str))
