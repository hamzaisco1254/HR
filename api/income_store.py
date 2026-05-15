"""
Customer invoice / income store — Postgres-backed.

Tracks revenue-generating outgoing invoices we issue to clients.
Auto-converts source currency to TND at create time using the current
ExchangeRates singleton, and stores the rate used so historical totals
remain stable when the rate changes later.

Schema (customer_invoices table) is bootstrapped by db_schema.py.
"""
import uuid
from datetime import datetime, date
from typing import Optional, List, Dict

import db


VALID_CURRENCIES = ('EUR', 'TND', 'USD', 'GBP', 'CHF')
VALID_STATUSES   = ('draft', 'sent', 'paid', 'overdue', 'cancelled')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _norm_currency(value: str) -> str:
    code = (value or '').strip().upper()
    return code if code in VALID_CURRENCIES else 'EUR'


def _norm_status(value: str) -> str:
    v = (value or '').strip().lower()
    return v if v in VALID_STATUSES else 'draft'


def _date(value):
    if value in (None, ''):
        return None
    return value


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_of_month(value):
    """Coerce any 'YYYY-MM' or 'YYYY-MM-DD' into a date set to the 1st."""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return date(value.year, value.month, 1)
    if isinstance(value, datetime):
        return date(value.year, value.month, 1)
    s = str(value).strip()
    if len(s) >= 7 and s[4] == '-':
        try:
            year  = int(s[0:4])
            month = int(s[5:7])
            return date(year, month, 1)
        except (ValueError, TypeError):
            return None
    return None


def _row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    def _dt(v):
        if v is None:
            return ''
        if hasattr(v, 'isoformat'):
            return v.isoformat()
        return str(v)
    return {
        'id':            r['id'],
        'invoice_ref':   r.get('invoice_ref') or '',
        'client_id':     r.get('client_id'),
        'project_id':    r.get('project_id'),
        'department_id': r.get('department_id'),
        'period_month':  _dt(r.get('period_month')),
        'effort_days':   float(r['effort_days']) if r.get('effort_days') is not None else None,
        'issue_date':    _dt(r.get('issue_date')),
        'due_date':      _dt(r.get('due_date')),
        'amount':        float(r.get('amount') or 0),
        'currency':      r.get('currency') or 'EUR',
        'fx_rate':       float(r['fx_rate']) if r.get('fx_rate') is not None else None,
        'amount_tnd':    float(r['amount_tnd']) if r.get('amount_tnd') is not None else None,
        'received_tnd':  float(r['received_tnd']) if r.get('received_tnd') is not None else None,
        'status':        r.get('status') or 'draft',
        'sent_at':       _dt(r.get('sent_at')),
        'paid_at':       _dt(r.get('paid_at')),
        'notes':         r.get('notes') or '',
        'pdf_filename':  r.get('pdf_filename') or '',
        'created_at':    _dt(r.get('created_at')),
        'updated_at':    _dt(r.get('updated_at')),
    }


class IncomeStore:
    """CRUD + aggregations for customer_invoices (income side)."""

    def __init__(self, fx_rates=None):
        # fx_rates is the shared ExchangeRates instance from financial_store.
        # Passed in by index.py at construction so we don't double-load the
        # kv_settings row.
        self.fx = fx_rates

    # ─── helpers ─────────────────────────────────────────────────

    def _compute_tnd(self, amount: float, currency: str, fx_rate_override: Optional[float]) -> tuple:
        """Return (fx_rate, amount_tnd). amount_tnd is None for ambiguous cases."""
        currency = _norm_currency(currency)
        if amount in (None, 0, 0.0):
            return (None, 0.0 if amount == 0 else None)
        if currency == 'TND':
            return (None, float(amount))
        if fx_rate_override is not None and fx_rate_override > 0:
            return (float(fx_rate_override), float(amount) * float(fx_rate_override))
        rate = self.fx.get_rate(currency, 'TND') if self.fx else 1.0
        if rate <= 0:
            return (None, None)
        return (float(rate), float(amount) * float(rate))

    # ─── CRUD ────────────────────────────────────────────────────

    def list(self, filters: Optional[Dict] = None) -> List[Dict]:
        sql = """
            SELECT ci.*,
                   c.name AS client_name,
                   d.name AS department_name,
                   p.name AS project_name,
                   p.code AS project_code
              FROM customer_invoices ci
              LEFT JOIN clients     c ON c.id = ci.client_id
              LEFT JOIN departments d ON d.id = ci.department_id
              LEFT JOIN projects    p ON p.id = ci.project_id
        """
        clauses, params = [], []
        f = filters or {}
        if f.get('status'):
            clauses.append("ci.status = %s"); params.append(f['status'])
        if f.get('client_id'):
            clauses.append("ci.client_id = %s"); params.append(f['client_id'])
        if f.get('department_id'):
            clauses.append("ci.department_id = %s"); params.append(f['department_id'])
        if f.get('project_id'):
            clauses.append("ci.project_id = %s"); params.append(f['project_id'])
        if f.get('period_from'):
            pm = _first_of_month(f['period_from'])
            if pm: clauses.append("ci.period_month >= %s"); params.append(pm)
        if f.get('period_to'):
            pm = _first_of_month(f['period_to'])
            if pm: clauses.append("ci.period_month <= %s"); params.append(pm)
        if f.get('year'):
            try:
                y = int(f['year'])
                clauses.append("EXTRACT(YEAR FROM ci.period_month) = %s"); params.append(y)
            except (TypeError, ValueError):
                pass

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ci.period_month DESC, ci.created_at DESC"

        rows = db.query(sql, tuple(params) if params else None)
        out = []
        for r in rows:
            row = _row(r) or {}
            row['client_name']     = r.get('client_name') or ''
            row['department_name'] = r.get('department_name') or ''
            row['project_name']    = r.get('project_name') or ''
            row['project_code']    = r.get('project_code') or ''
            out.append(row)
        return out

    def get(self, iid: str) -> Optional[Dict]:
        if not iid:
            return None
        return _row(db.one("SELECT * FROM customer_invoices WHERE id = %s", (iid,)))

    def add(self, data: Dict, created_by: Optional[str] = None) -> Dict:
        client_id = (data.get('client_id') or '').strip()
        if not client_id or not db.one("SELECT 1 FROM clients WHERE id = %s", (client_id,)):
            raise ValueError("Client introuvable.")

        period_month = _first_of_month(data.get('period_month'))
        if not period_month:
            raise ValueError("Mois de facturation requis (format YYYY-MM).")

        try:
            amount = float(data.get('amount') or 0)
        except (TypeError, ValueError):
            raise ValueError("Montant invalide.")

        currency = _norm_currency(data.get('currency', 'EUR'))
        fx_override = _decimal(data.get('fx_rate'))
        fx_rate, amount_tnd = self._compute_tnd(amount, currency, fx_override)

        invoice_ref = (data.get('invoice_ref') or '').strip()
        if invoice_ref:
            dup = db.one("SELECT id FROM customer_invoices WHERE invoice_ref = %s", (invoice_ref,))
            if dup:
                raise ValueError(f"Une facture avec la reference '{invoice_ref}' existe deja.")

        iid = _new_id()
        db.execute(
            """INSERT INTO customer_invoices (
                 id, invoice_ref, client_id, project_id, department_id,
                 period_month, effort_days, issue_date, due_date,
                 amount, currency, fx_rate, amount_tnd, received_tnd,
                 status, sent_at, paid_at, notes, pdf_filename,
                 created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s)""",
            (iid,
             invoice_ref or None,
             client_id,
             data.get('project_id') or None,
             data.get('department_id') or None,
             period_month,
             _decimal(data.get('effort_days')),
             _date(data.get('issue_date')),
             _date(data.get('due_date')),
             amount,
             currency,
             fx_rate,
             amount_tnd,
             _decimal(data.get('received_tnd')),
             _norm_status(data.get('status', 'draft')),
             _date(data.get('sent_at')),
             _date(data.get('paid_at')),
             (data.get('notes') or '').strip() or None,
             (data.get('pdf_filename') or '').strip() or None,
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(iid) or {'id': iid}

    def update(self, iid: str, patch: Dict) -> Optional[Dict]:
        current = self.get(iid)
        if not current:
            return None
        sets, params = [], []

        if 'invoice_ref' in patch:
            v = (patch['invoice_ref'] or '').strip()
            if v:
                dup = db.one(
                    "SELECT id FROM customer_invoices WHERE invoice_ref = %s AND id <> %s",
                    (v, iid),
                )
                if dup:
                    raise ValueError(f"Une autre facture utilise deja la reference '{v}'.")
            sets.append("invoice_ref = %s"); params.append(v or None)

        if 'client_id' in patch:
            v = (patch['client_id'] or '').strip()
            if not v or not db.one("SELECT 1 FROM clients WHERE id = %s", (v,)):
                raise ValueError("Client introuvable.")
            sets.append("client_id = %s"); params.append(v)

        for f in ('project_id', 'department_id'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(patch[f] or None)

        if 'period_month' in patch:
            pm = _first_of_month(patch['period_month'])
            if not pm:
                raise ValueError("Mois de facturation invalide.")
            sets.append("period_month = %s"); params.append(pm)

        if 'effort_days' in patch:
            sets.append("effort_days = %s"); params.append(_decimal(patch['effort_days']))

        for f in ('issue_date', 'due_date', 'sent_at', 'paid_at'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(_date(patch[f]))

        # Amount / currency recompute amount_tnd unless explicit fx_rate
        amount_changed   = 'amount' in patch
        currency_changed = 'currency' in patch
        fx_in_patch      = 'fx_rate' in patch

        if amount_changed:
            try:
                amt = float(patch['amount'] or 0)
            except (TypeError, ValueError):
                raise ValueError("Montant invalide.")
            sets.append("amount = %s"); params.append(amt)
        if currency_changed:
            sets.append("currency = %s"); params.append(_norm_currency(patch['currency']))

        if amount_changed or currency_changed or fx_in_patch:
            amt = float(patch['amount']) if amount_changed else float(current.get('amount') or 0)
            cur = _norm_currency(patch['currency']) if currency_changed else current.get('currency', 'EUR')
            override = _decimal(patch['fx_rate']) if fx_in_patch else None
            fx_rate, amount_tnd = self._compute_tnd(amt, cur, override)
            sets.append("fx_rate = %s");    params.append(fx_rate)
            sets.append("amount_tnd = %s"); params.append(amount_tnd)

        if 'received_tnd' in patch:
            sets.append("received_tnd = %s"); params.append(_decimal(patch['received_tnd']))

        if 'status' in patch:
            new_status = _norm_status(patch['status'])
            sets.append("status = %s"); params.append(new_status)
            # If transitioning to 'paid' and paid_at not provided, stamp today
            if new_status == 'paid' and 'paid_at' not in patch and not current.get('paid_at'):
                sets.append("paid_at = %s"); params.append(datetime.utcnow().date())

        if 'notes' in patch:
            sets.append("notes = %s"); params.append((patch['notes'] or '').strip() or None)
        if 'pdf_filename' in patch:
            sets.append("pdf_filename = %s"); params.append((patch['pdf_filename'] or '').strip() or None)

        if not sets:
            return current

        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(iid)
        db.execute(f"UPDATE customer_invoices SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(iid)

    def mark_paid(self, iid: str, received_tnd: Optional[float] = None,
                  paid_at: Optional[str] = None) -> Optional[Dict]:
        patch = {'status': 'paid'}
        if received_tnd is not None:
            patch['received_tnd'] = received_tnd
        if paid_at:
            patch['paid_at'] = paid_at
        return self.update(iid, patch)

    def delete(self, iid: str) -> bool:
        return db.execute("DELETE FROM customer_invoices WHERE id = %s", (iid,)) > 0

    # ─── KPIs / aggregations ────────────────────────────────────

    def monthly_summary(self, year: Optional[int] = None) -> Dict:
        """Per-month totals in TND for the requested year (defaults to current)."""
        if year is None:
            year = datetime.utcnow().year
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = datetime.utcnow().year

        rows = db.query(
            """SELECT EXTRACT(MONTH FROM period_month)::int AS month,
                      SUM(amount)         AS total_invoiced,
                      SUM(amount_tnd)     AS expected_tnd,
                      SUM(received_tnd)   AS received_tnd,
                      COUNT(*)            AS invoice_count
                 FROM customer_invoices
                WHERE EXTRACT(YEAR FROM period_month) = %s
                GROUP BY EXTRACT(MONTH FROM period_month)
                ORDER BY month""",
            (year,),
        )
        by_month = {}
        for r in rows:
            m = int(r['month'])
            by_month[m] = {
                'month':           m,
                'total_invoiced':  float(r.get('total_invoiced') or 0),
                'expected_tnd':    float(r.get('expected_tnd') or 0),
                'received_tnd':    float(r.get('received_tnd') or 0),
                'invoice_count':   int(r.get('invoice_count') or 0),
            }
        # Fill missing months
        for m in range(1, 13):
            by_month.setdefault(m, {
                'month': m, 'total_invoiced': 0.0, 'expected_tnd': 0.0,
                'received_tnd': 0.0, 'invoice_count': 0,
            })
        return {
            'year':     year,
            'months':   [by_month[m] for m in range(1, 13)],
            'total':    {
                'expected_tnd': sum(v['expected_tnd'] for v in by_month.values()),
                'received_tnd': sum(v['received_tnd'] for v in by_month.values()),
                'invoice_count': sum(v['invoice_count'] for v in by_month.values()),
            },
        }

    def by_department(self, year: Optional[int] = None) -> List[Dict]:
        if year is None:
            year = datetime.utcnow().year
        rows = db.query(
            """SELECT ci.department_id, d.name AS department_name,
                      SUM(ci.amount_tnd)   AS expected_tnd,
                      SUM(ci.received_tnd) AS received_tnd,
                      COUNT(*)             AS invoice_count
                 FROM customer_invoices ci
                 LEFT JOIN departments d ON d.id = ci.department_id
                WHERE EXTRACT(YEAR FROM ci.period_month) = %s
                GROUP BY ci.department_id, d.name
                ORDER BY expected_tnd DESC NULLS LAST""",
            (int(year),),
        )
        return [{
            'department_id':   r.get('department_id'),
            'department_name': r.get('department_name') or 'Non assignee',
            'expected_tnd':    float(r.get('expected_tnd') or 0),
            'received_tnd':    float(r.get('received_tnd') or 0),
            'invoice_count':   int(r.get('invoice_count') or 0),
        } for r in rows]

    def by_client(self, year: Optional[int] = None) -> List[Dict]:
        if year is None:
            year = datetime.utcnow().year
        rows = db.query(
            """SELECT ci.client_id, c.name AS client_name,
                      SUM(ci.amount_tnd)   AS expected_tnd,
                      SUM(ci.received_tnd) AS received_tnd,
                      COUNT(*)             AS invoice_count
                 FROM customer_invoices ci
                 LEFT JOIN clients c ON c.id = ci.client_id
                WHERE EXTRACT(YEAR FROM ci.period_month) = %s
                GROUP BY ci.client_id, c.name
                ORDER BY expected_tnd DESC NULLS LAST""",
            (int(year),),
        )
        return [{
            'client_id':     r.get('client_id'),
            'client_name':   r.get('client_name') or 'Inconnu',
            'expected_tnd':  float(r.get('expected_tnd') or 0),
            'received_tnd':  float(r.get('received_tnd') or 0),
            'invoice_count': int(r.get('invoice_count') or 0),
        } for r in rows]

    def overdue(self) -> List[Dict]:
        """Invoices past due_date that are not paid."""
        today = datetime.utcnow().date()
        rows = db.query(
            """SELECT ci.*, c.name AS client_name
                 FROM customer_invoices ci
                 LEFT JOIN clients c ON c.id = ci.client_id
                WHERE ci.status IN ('sent', 'draft', 'overdue')
                  AND ci.due_date IS NOT NULL
                  AND ci.due_date < %s
                ORDER BY ci.due_date ASC""",
            (today,),
        )
        out = []
        for r in rows:
            row = _row(r) or {}
            row['client_name'] = r.get('client_name') or ''
            row['days_overdue'] = (today - r['due_date']).days if r.get('due_date') else 0
            out.append(row)
        return out
