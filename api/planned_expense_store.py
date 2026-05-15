"""
Planned / recurring expenses store — Postgres-backed.

Captures commitments that aren't yet vendor invoices: rent, software
subscriptions, planned hardware purchases, taxes, social contributions
due, etc. The statements_engine reads these to build forward-looking
P&L lines and variance reports.

Two flavors per row:
  - One-time:  is_recurring=False, due_date=YYYY-MM-DD, end_date=NULL
  - Recurring: is_recurring=True, start_date + end_date (NULL = open-ended),
                frequency in {monthly,quarterly,yearly}

The `occurrences()` method expands a recurring row into individual
months/quarters within a requested window, so callers can group them
by period without re-implementing the calendar logic.
"""
import uuid
from datetime import datetime, date
from typing import Optional, List, Dict

import db


VALID_CURRENCIES = ('TND', 'EUR', 'USD', 'GBP', 'CHF')
VALID_STATUSES   = ('planned', 'paid', 'cancelled')
VALID_FREQUENCIES = ('monthly', 'quarterly', 'yearly')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _norm_currency(value: str) -> str:
    code = (value or '').strip().upper()
    return code if code in VALID_CURRENCIES else 'TND'


def _norm_status(value: str) -> str:
    v = (value or '').strip().lower()
    return v if v in VALID_STATUSES else 'planned'


def _norm_frequency(value: str) -> Optional[str]:
    v = (value or '').strip().lower()
    return v if v in VALID_FREQUENCIES else None


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


def _row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    def _dt(v):
        if v is None: return ''
        if hasattr(v, 'isoformat'): return v.isoformat()
        return str(v)
    return {
        'id':                r['id'],
        'name':              r['name'],
        'category':          r.get('category') or 'autre',
        'amount':            float(r.get('amount') or 0),
        'currency':          r.get('currency') or 'TND',
        'fx_rate':           float(r['fx_rate']) if r.get('fx_rate') is not None else None,
        'amount_tnd':        float(r['amount_tnd']) if r.get('amount_tnd') is not None else None,
        'is_recurring':      bool(r.get('is_recurring')),
        'frequency':         r.get('frequency') or '',
        'start_date':        _dt(r.get('start_date')),
        'end_date':          _dt(r.get('end_date')),
        'due_date':          _dt(r.get('due_date')),
        'status':            r.get('status') or 'planned',
        'department_id':     r.get('department_id'),
        'linked_invoice_id': r.get('linked_invoice_id'),
        'notes':             r.get('notes') or '',
        'created_at':        _dt(r.get('created_at')),
        'updated_at':        _dt(r.get('updated_at')),
    }


def _add_months(d: date, n: int) -> date:
    """Add n months to a date, clamping the day if needed."""
    month_total = d.month - 1 + n
    year = d.year + month_total // 12
    month = month_total % 12 + 1
    # Clamp day to month length
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


class PlannedExpenseStore:
    def __init__(self, fx_rates=None):
        self.fx = fx_rates

    # ─── helpers ────────────────────────────────────────────────

    def _compute_tnd(self, amount: float, currency: str, fx_override: Optional[float]) -> tuple:
        currency = _norm_currency(currency)
        if amount in (None, 0, 0.0):
            return (None, 0.0 if amount == 0 else None)
        if currency == 'TND':
            return (None, float(amount))
        if fx_override is not None and fx_override > 0:
            return (float(fx_override), float(amount) * float(fx_override))
        rate = self.fx.get_rate(currency, 'TND') if self.fx else 1.0
        if rate <= 0:
            return (None, None)
        return (float(rate), float(amount) * float(rate))

    # ─── CRUD ───────────────────────────────────────────────────

    def list(self, filters: Optional[Dict] = None) -> List[Dict]:
        sql = """
            SELECT pe.*, d.name AS department_name
              FROM planned_expenses pe
              LEFT JOIN departments d ON d.id = pe.department_id
        """
        clauses, params = [], []
        f = filters or {}
        if f.get('status'):
            clauses.append("pe.status = %s"); params.append(f['status'])
        if f.get('category'):
            clauses.append("pe.category = %s"); params.append(f['category'])
        if f.get('year'):
            try:
                y = int(f['year'])
                # Overlap with the year: one-time due in year OR recurring window touches year
                clauses.append(
                    "((pe.due_date IS NOT NULL AND EXTRACT(YEAR FROM pe.due_date) = %s) "
                    " OR (pe.is_recurring = TRUE AND "
                    "     COALESCE(pe.start_date, %s) <= %s AND "
                    "     COALESCE(pe.end_date,   %s) >= %s))"
                )
                params.extend([y, date(y, 12, 31), date(y, 12, 31), date(y, 1, 1), date(y, 1, 1)])
            except (TypeError, ValueError):
                pass

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY pe.status ASC, COALESCE(pe.due_date, pe.start_date) ASC, pe.created_at DESC"

        rows = db.query(sql, tuple(params) if params else None)
        out = []
        for r in rows:
            row = _row(r) or {}
            row['department_name'] = r.get('department_name') or ''
            out.append(row)
        return out

    def get(self, eid: str) -> Optional[Dict]:
        if not eid:
            return None
        return _row(db.one("SELECT * FROM planned_expenses WHERE id = %s", (eid,)))

    def add(self, data: Dict, created_by: Optional[str] = None) -> Dict:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError("Nom de la depense requis.")

        is_recurring = bool(data.get('is_recurring'))
        frequency    = _norm_frequency(data.get('frequency')) if is_recurring else None
        start_date   = _date(data.get('start_date'))
        end_date     = _date(data.get('end_date'))
        due_date     = _date(data.get('due_date'))

        if is_recurring:
            if not frequency:
                raise ValueError("Frequence requise (monthly/quarterly/yearly) pour une depense recurrente.")
            if not start_date:
                raise ValueError("Date de debut requise pour une depense recurrente.")
        else:
            if not due_date:
                raise ValueError("Date d'echeance requise pour une depense ponctuelle.")

        try:
            amount = float(data.get('amount') or 0)
        except (TypeError, ValueError):
            raise ValueError("Montant invalide.")
        currency    = _norm_currency(data.get('currency', 'TND'))
        fx_override = _decimal(data.get('fx_rate'))
        fx_rate, amount_tnd = self._compute_tnd(amount, currency, fx_override)

        eid = _new_id()
        db.execute(
            """INSERT INTO planned_expenses (
                 id, name, category, amount, currency, fx_rate, amount_tnd,
                 is_recurring, frequency, start_date, end_date, due_date,
                 status, department_id, linked_invoice_id, notes,
                 created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s)""",
            (eid, name,
             (data.get('category') or 'autre').strip().lower() or 'autre',
             amount, currency, fx_rate, amount_tnd,
             is_recurring, frequency, start_date, end_date, due_date,
             _norm_status(data.get('status', 'planned')),
             data.get('department_id') or None,
             data.get('linked_invoice_id') or None,
             (data.get('notes') or '').strip() or None,
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(eid) or {'id': eid}

    def update(self, eid: str, patch: Dict) -> Optional[Dict]:
        current = self.get(eid)
        if not current:
            return None

        sets, params = [], []

        for f in ('name', 'category', 'notes'):
            if f in patch:
                v = (patch[f] or '').strip()
                if f == 'name' and not v:
                    raise ValueError("Nom requis.")
                if f == 'category':
                    v = v.lower() or 'autre'
                sets.append(f"{f} = %s")
                params.append(v if v else None)

        for f in ('department_id', 'linked_invoice_id'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(patch[f] or None)

        if 'is_recurring' in patch:
            sets.append("is_recurring = %s"); params.append(bool(patch['is_recurring']))
        if 'frequency' in patch:
            sets.append("frequency = %s"); params.append(_norm_frequency(patch['frequency']))

        for f in ('start_date', 'end_date', 'due_date'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(_date(patch[f]))

        # Amount / currency / fx recompute amount_tnd
        amt_changed = 'amount' in patch
        cur_changed = 'currency' in patch
        fx_changed  = 'fx_rate' in patch

        if amt_changed:
            try: a = float(patch['amount'] or 0)
            except (TypeError, ValueError): raise ValueError("Montant invalide.")
            sets.append("amount = %s"); params.append(a)
        if cur_changed:
            sets.append("currency = %s"); params.append(_norm_currency(patch['currency']))

        if amt_changed or cur_changed or fx_changed:
            a   = float(patch['amount']) if amt_changed else float(current.get('amount') or 0)
            cur = _norm_currency(patch['currency']) if cur_changed else current.get('currency', 'TND')
            ov  = _decimal(patch['fx_rate']) if fx_changed else None
            fx_rate, amount_tnd = self._compute_tnd(a, cur, ov)
            sets.append("fx_rate = %s");    params.append(fx_rate)
            sets.append("amount_tnd = %s"); params.append(amount_tnd)

        if 'status' in patch:
            sets.append("status = %s"); params.append(_norm_status(patch['status']))

        if not sets:
            return current

        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(eid)
        db.execute(f"UPDATE planned_expenses SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(eid)

    def delete(self, eid: str) -> bool:
        return db.execute("DELETE FROM planned_expenses WHERE id = %s", (eid,)) > 0

    def materialize(self, eid: str, invoice_id: str) -> Optional[Dict]:
        """Mark a planned expense as paid + link to the vendor invoice that materialized it."""
        if not db.one("SELECT 1 FROM invoices WHERE id = %s", (invoice_id,)):
            raise ValueError("Facture introuvable.")
        return self.update(eid, {'status': 'paid', 'linked_invoice_id': invoice_id})

    # ─── Occurrence expansion ────────────────────────────────────

    def occurrences(self, period_start: date, period_end: date,
                    only_pending: bool = True) -> List[Dict]:
        """Expand all planned expenses into per-occurrence rows within
        the [period_start, period_end] window inclusive.

        Each row represents one cash outflow (or commitment) on a
        specific date. For recurring entries, multiple rows are emitted.
        For one-time entries with due_date in the window, a single row.

        If `only_pending` is True, skip rows where status='paid' or
        'cancelled', and skip rows already linked to a materialized
        invoice (to avoid double-counting with the real invoices table).
        """
        rows = db.query("SELECT * FROM planned_expenses")
        out = []
        for r in rows:
            r_dict = _row(r) or {}
            if only_pending and (r_dict['status'] != 'planned' or r_dict['linked_invoice_id']):
                continue

            if not r_dict['is_recurring']:
                d = r['due_date']
                if d and period_start <= d <= period_end:
                    out.append({
                        'id':          r_dict['id'],
                        'name':        r_dict['name'],
                        'category':    r_dict['category'],
                        'occurrence_date': d.isoformat(),
                        'amount_tnd':  r_dict['amount_tnd'] or 0.0,
                        'department_id': r_dict['department_id'],
                        'recurring':   False,
                    })
                continue

            # Recurring: walk forward in cadence steps
            freq = r_dict['frequency']
            step_months = {'monthly': 1, 'quarterly': 3, 'yearly': 12}.get(freq, 1)
            s = r['start_date']
            e = r['end_date'] or period_end
            if not s:
                continue
            cur = s
            # Fast-forward to first occurrence >= period_start
            while cur < period_start:
                cur = _add_months(cur, step_months)
                if cur > e:
                    break
            while cur <= e and cur <= period_end:
                out.append({
                    'id':          r_dict['id'],
                    'name':        r_dict['name'],
                    'category':    r_dict['category'],
                    'occurrence_date': cur.isoformat(),
                    'amount_tnd':  r_dict['amount_tnd'] or 0.0,
                    'department_id': r_dict['department_id'],
                    'recurring':   True,
                })
                cur = _add_months(cur, step_months)
        return out

    def total_for_period(self, period_start: date, period_end: date) -> float:
        return sum(o['amount_tnd'] for o in self.occurrences(period_start, period_end))

    def by_category_for_period(self, period_start: date, period_end: date) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for o in self.occurrences(period_start, period_end):
            out[o['category']] = out.get(o['category'], 0.0) + o['amount_tnd']
        return out


# ─── Finance rates (CNSS, corporate tax) — kv_settings backed ────────

_RATES_KEY = 'finance_rates'
_RATES_DEFAULTS = {
    'cnss_employer_rate': 16.57,   # % of gross salary, Tunisia 2025
    'cnss_employee_rate':  9.18,   # informational only (already netted from gross)
    'corporate_tax_rate': 15.0,    # % corporate income tax (Tunisia base rate; financial sector higher)
}


def get_finance_rates() -> Dict[str, float]:
    """Read finance rate settings, returning defaults if not configured."""
    import json
    row = db.one("SELECT value FROM kv_settings WHERE key = %s", (_RATES_KEY,))
    if row and isinstance(row.get('value'), dict):
        merged = dict(_RATES_DEFAULTS)
        for k, v in row['value'].items():
            try:
                merged[k] = float(v)
            except (TypeError, ValueError):
                pass
        return merged
    return dict(_RATES_DEFAULTS)


def set_finance_rates(updates: Dict[str, float]) -> Dict[str, float]:
    """Update one or more finance rates. Unknown keys are ignored."""
    import json
    current = get_finance_rates()
    for k, v in (updates or {}).items():
        if k not in _RATES_DEFAULTS:
            continue
        try:
            current[k] = float(v)
        except (TypeError, ValueError):
            continue
    db.execute(
        """INSERT INTO kv_settings (key, value, updated_at)
           VALUES (%s, %s::jsonb, %s)
           ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
        (_RATES_KEY, json.dumps(current), datetime.utcnow()),
    )
    return current
