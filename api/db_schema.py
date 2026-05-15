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
    clients              external clients we bill (PR-1 finance foundation)
    departments          internal departments
    employees            employees as DB-backed records
    projects             revenue-bearing projects, linked to a client
    project_assignments  N:M employee <-> project allocation, with %
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

-- Invoice category (AI-classified). Stored as a normalized code (e.g.
-- 'loyer', 'logiciels'). Free-text on purpose — the canonical category
-- list lives in invoice_processor.INVOICE_CATEGORIES so new categories
-- can be added in code without a migration. NULL = not yet classified.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS category TEXT;
CREATE INDEX IF NOT EXISTS invoices_category_idx ON invoices (category);

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

-- ════════════════════════════════════════════════════════════════
-- PR-1: Organization foundation (clients, departments, employees,
-- projects, project_assignments). Finance and revenue tables build
-- on top of these in subsequent PRs.
-- ════════════════════════════════════════════════════════════════

-- ── Clients (customers we bill) ────────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    legal_name        TEXT,
    country           TEXT NOT NULL DEFAULT 'TN',     -- ISO 3166-1 alpha-2
    default_currency  TEXT NOT NULL DEFAULT 'EUR',
    vat_id            TEXT,                            -- "DE123456789", etc.
    email             TEXT,
    phone             TEXT,
    address           TEXT,
    notes             TEXT,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_by        TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS clients_name_lower_idx ON clients (LOWER(name));
CREATE INDEX IF NOT EXISTS clients_active_idx ON clients (active);

-- ── Departments (internal org units) ───────────────────────────
CREATE TABLE IF NOT EXISTS departments (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    cost_center  TEXT,                                  -- optional GL code
    manager_id   TEXT,                                  -- FK to employees, set after employees exist
    notes        TEXT,
    created_by   TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS departments_name_lower_idx ON departments (LOWER(name));

-- ── Employees ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS employees (
    id               TEXT PRIMARY KEY,
    matricule        TEXT,                              -- internal employee number
    cin              TEXT,                              -- carte d'identite
    full_name        TEXT NOT NULL,
    email            TEXT,
    phone            TEXT,
    position         TEXT,
    department_id    TEXT REFERENCES departments(id) ON DELETE SET NULL,
    manager_id       TEXT REFERENCES employees(id) ON DELETE SET NULL,
    start_date       DATE,
    end_date         DATE,                              -- null = active
    monthly_salary   NUMERIC(15, 2),                    -- gross
    salary_currency  TEXT NOT NULL DEFAULT 'TND',
    user_id          TEXT REFERENCES users(id) ON DELETE SET NULL,
    notes            TEXT,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_by       TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS employees_dept_idx     ON employees (department_id);
CREATE INDEX IF NOT EXISTS employees_active_idx   ON employees (active);
CREATE INDEX IF NOT EXISTS employees_matricule_idx ON employees (matricule);

-- Late-bound FK from departments.manager_id -> employees.id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'departments_manager_fk'
    ) THEN
        ALTER TABLE departments
            ADD CONSTRAINT departments_manager_fk
            FOREIGN KEY (manager_id) REFERENCES employees(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── Projects ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id                 TEXT PRIMARY KEY,
    code               TEXT,                            -- e.g. "PRJ-2026-001"
    name               TEXT NOT NULL,
    client_id          TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    description        TEXT,
    start_date         DATE,
    end_date           DATE,
    status             TEXT NOT NULL DEFAULT 'active',  -- active | paused | closed
    contract_value     NUMERIC(15, 2),                  -- total contract amount
    contract_currency  TEXT NOT NULL DEFAULT 'EUR',
    notes              TEXT,
    created_by         TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_code_unique ON projects (code) WHERE code IS NOT NULL AND code <> '';
CREATE INDEX IF NOT EXISTS projects_client_idx ON projects (client_id);
CREATE INDEX IF NOT EXISTS projects_status_idx ON projects (status);

-- ── Customer invoices (income / outgoing invoices) ─────────────
-- The revenue side of the business. Each row is one billing event:
-- typically a monthly invoice we issue to a client. EUR/TND conversion
-- is computed at create time using current ExchangeRates, stored
-- alongside the original amount so historical totals are stable when
-- the rate changes later.
CREATE TABLE IF NOT EXISTS customer_invoices (
    id              TEXT PRIMARY KEY,
    invoice_ref     TEXT,                                 -- our outgoing invoice number
    client_id       TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    project_id      TEXT REFERENCES projects(id) ON DELETE SET NULL,
    department_id   TEXT REFERENCES departments(id) ON DELETE SET NULL,
    period_month    DATE NOT NULL,                        -- 1st of billing month
    effort_days     NUMERIC(7, 2),                        -- estimated person-days
    issue_date      DATE,
    due_date        DATE,
    amount          NUMERIC(15, 2) NOT NULL DEFAULT 0,    -- invoiced amount
    currency        TEXT NOT NULL DEFAULT 'EUR',          -- source currency
    fx_rate         NUMERIC(15, 6),                       -- source→TND rate used (NULL if currency=TND)
    amount_tnd      NUMERIC(15, 2),                       -- expected TND equivalent
    received_tnd    NUMERIC(15, 2),                       -- actually received in TND (when paid)
    status          TEXT NOT NULL DEFAULT 'draft',        -- draft|sent|paid|overdue|cancelled
    sent_at         DATE,
    paid_at         DATE,
    notes           TEXT,
    pdf_filename    TEXT,                                  -- attached invoice file name (no blob)
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cust_inv_period_idx     ON customer_invoices (period_month DESC);
CREATE INDEX IF NOT EXISTS cust_inv_client_idx     ON customer_invoices (client_id);
CREATE INDEX IF NOT EXISTS cust_inv_dept_idx       ON customer_invoices (department_id);
CREATE INDEX IF NOT EXISTS cust_inv_project_idx    ON customer_invoices (project_id);
CREATE INDEX IF NOT EXISTS cust_inv_status_idx     ON customer_invoices (status);
CREATE UNIQUE INDEX IF NOT EXISTS cust_inv_ref_unique ON customer_invoices (invoice_ref) WHERE invoice_ref IS NOT NULL AND invoice_ref <> '';

-- ── Project assignments (N:M employee <-> project) ─────────────
CREATE TABLE IF NOT EXISTS project_assignments (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    employee_id     TEXT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    allocation_pct  NUMERIC(5, 2) NOT NULL CHECK (allocation_pct >= 0 AND allocation_pct <= 100),
    role            TEXT,                                -- "Lead developer", "QA", etc.
    start_date      DATE NOT NULL,
    end_date        DATE,                                -- null = ongoing
    hourly_rate     NUMERIC(15, 2),                      -- optional
    rate_currency   TEXT NOT NULL DEFAULT 'EUR',
    notes           TEXT,
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS proj_assign_project_idx  ON project_assignments (project_id);
CREATE INDEX IF NOT EXISTS proj_assign_employee_idx ON project_assignments (employee_id);
CREATE INDEX IF NOT EXISTS proj_assign_period_idx   ON project_assignments (employee_id, start_date, end_date);
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
