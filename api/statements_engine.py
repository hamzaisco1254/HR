"""
Financial Statements Engine — French-style P&L, cash flow, breakdowns.

Pulls data from:
    customer_invoices  -> revenue side (PR-4)
    invoices           -> expense side (vendor invoices, multi-currency)
    employees          -> salary aggregate (estimate)
    payments           -> bank movements (used opportunistically)

All monetary amounts are normalized to TND at query time using the
shared ExchangeRates singleton. Customer invoices store fx_rate +
amount_tnd at create time so their TND value is rate-stable; vendor
invoices currently don't, so they're converted at current rates and
the UI shows a disclaimer.

Public surface:
    compute_pnl(year, month=None, basis='accrual')
    compute_cashflow(year)
    compute_revenue_breakdown(year)
    compute_expense_breakdown(year)
    compute_margins(year, month=None)
"""
from datetime import datetime, date
from typing import Dict, List, Optional

import db


# Category → P&L bucket mapping (French standard)
# - charges_externes:  external services, suppliers, materials
# - charges_personnel: salaries (note: we also add the employees.monthly_salary aggregate)
# - taxes:             government taxes and contributions
# - charges_financieres: bank fees, interest
_CATEGORY_BUCKETS = {
    'loyer':       'charges_externes',
    'logiciels':   'charges_externes',
    'telecoms':    'charges_externes',
    'marketing':   'charges_externes',
    'voyage':      'charges_externes',
    'materiel':    'charges_externes',
    'fournitures': 'charges_externes',
    'honoraires':  'charges_externes',
    'energie':     'charges_externes',
    'maintenance': 'charges_externes',
    'autre':       'charges_externes',
    'salaires':    'charges_personnel',
    'taxes':       'taxes',
    'banque':      'charges_financieres',
}

# Display labels (French) for the buckets, in canonical P&L order
_BUCKET_LABELS = [
    ('charges_externes',    'Charges externes'),
    ('charges_personnel',   'Charges de personnel'),
    ('taxes',               'Impots et taxes'),
    ('charges_financieres', 'Charges financieres'),
]


def _norm(amount, fx_rates, currency: str) -> float:
    """Convert any amount to TND using the shared ExchangeRates."""
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    if not currency or currency.upper() == 'TND':
        return amt
    return fx_rates.to_tnd(amt, currency) if fx_rates else amt


def _months_in_period(year: int, month: Optional[int]) -> int:
    """How many months the requested period covers (for salary aggregation)."""
    if month:
        return 1
    # Year: if it's the current year, only count months elapsed
    today = datetime.utcnow().date()
    if year == today.year:
        return max(1, today.month)
    return 12


# ─── Revenue (customer_invoices side) ────────────────────────────────

def _revenue_rows(year: int, month: Optional[int]) -> List[dict]:
    sql = "SELECT * FROM customer_invoices WHERE EXTRACT(YEAR FROM period_month) = %s"
    params: list = [year]
    if month:
        sql += " AND EXTRACT(MONTH FROM period_month) = %s"
        params.append(month)
    return db.query(sql, tuple(params))


def _expense_rows(year: int, month: Optional[int]) -> List[dict]:
    """Vendor invoices in the period. invoice_date is the timeline anchor."""
    sql = "SELECT * FROM invoices WHERE invoice_date IS NOT NULL AND EXTRACT(YEAR FROM invoice_date) = %s"
    params: list = [year]
    if month:
        sql += " AND EXTRACT(MONTH FROM invoice_date) = %s"
        params.append(month)
    return db.query(sql, tuple(params))


def _salary_aggregate_tnd(fx_rates, months: int) -> float:
    """Sum active employees' monthly salaries (TND), multiplied by months elapsed."""
    rows = db.query(
        "SELECT monthly_salary, salary_currency FROM employees WHERE active = TRUE AND monthly_salary IS NOT NULL"
    )
    monthly_total = 0.0
    for r in rows:
        monthly_total += _norm(r.get('monthly_salary'), fx_rates, r.get('salary_currency') or 'TND')
    return monthly_total * months


# ─── Public API ──────────────────────────────────────────────────────

def compute_pnl(fx_rates, year: int, month: Optional[int] = None, basis: str = 'accrual') -> Dict:
    """French-style P&L for the period.

    basis='accrual': revenue = sum of customer_invoices.amount_tnd
    basis='cash':    revenue = sum of customer_invoices.received_tnd

    Returns:
        {
          period: {year, month, basis, months_covered},
          revenue: float,
          expenses_by_bucket: { bucket_id: {label, amount, items: [...]} },
          totals: {
            charges_externes, charges_personnel, taxes, charges_financieres,
            total_expenses, ebitda, operating_income, net_result
          },
          notes: [str, ...]
        }
    """
    rev_rows = _revenue_rows(year, month)
    if basis == 'cash':
        revenue = sum(float(r.get('received_tnd') or 0) for r in rev_rows)
    else:
        revenue = sum(float(r.get('amount_tnd') or 0) for r in rev_rows)

    exp_rows = _expense_rows(year, month)
    months_covered = _months_in_period(year, month)
    salary_est_tnd = _salary_aggregate_tnd(fx_rates, months_covered)

    # Bucket expenses by category
    buckets: Dict[str, Dict] = {bk: {'label': lbl, 'amount': 0.0, 'items': []}
                                  for bk, lbl in _BUCKET_LABELS}

    for inv in exp_rows:
        cat    = (inv.get('category') or '').lower()
        bucket = _CATEGORY_BUCKETS.get(cat, 'charges_externes')
        amt    = _norm(inv.get('amount'), fx_rates, inv.get('currency') or 'TND')
        buckets[bucket]['amount'] += amt
        buckets[bucket]['items'].append({
            'category': cat or 'autre',
            'amount_tnd': amt,
            'supplier_name': inv.get('supplier_name') or '',
            'invoice_date': str(inv.get('invoice_date') or ''),
        })

    # Add salary aggregate to charges_personnel
    if salary_est_tnd > 0:
        buckets['charges_personnel']['amount'] += salary_est_tnd
        buckets['charges_personnel']['items'].append({
            'category': 'salaires_estimes',
            'amount_tnd': salary_est_tnd,
            'supplier_name': f'Salaires (estimes, {months_covered} mois)',
            'invoice_date': '',
        })

    charges_externes      = buckets['charges_externes']['amount']
    charges_personnel     = buckets['charges_personnel']['amount']
    taxes_amt             = buckets['taxes']['amount']
    charges_financieres   = buckets['charges_financieres']['amount']
    total_expenses        = charges_externes + charges_personnel + taxes_amt + charges_financieres

    # French P&L cascade
    ebitda           = revenue - charges_externes - charges_personnel
    operating_income = ebitda  # no D&A modeled
    net_result       = operating_income - taxes_amt - charges_financieres

    notes = []
    if any(_norm(r.get('amount'), fx_rates, r.get('currency') or 'TND') != float(r.get('amount') or 0)
           for r in exp_rows):
        notes.append("Les depenses en devises etrangeres sont converties au taux actuel (non historise).")
    if salary_est_tnd > 0:
        notes.append(f"Les salaires sont estimes a partir des fiches employes ({months_covered} mois).")
    notes.append("Aucune dotation aux amortissements modelisee (resultat operationnel = EBITDA).")

    return {
        'period': {
            'year': year, 'month': month, 'basis': basis,
            'months_covered': months_covered,
        },
        'revenue': revenue,
        'expenses_by_bucket': buckets,
        'totals': {
            'charges_externes':    charges_externes,
            'charges_personnel':   charges_personnel,
            'taxes':               taxes_amt,
            'charges_financieres': charges_financieres,
            'total_expenses':      total_expenses,
            'ebitda':              ebitda,
            'operating_income':    operating_income,
            'net_result':          net_result,
        },
        'notes': notes,
    }


def compute_cashflow(fx_rates, year: int) -> Dict:
    """Monthly cash flow series — cash in/out over the year.

    Returns:
        {
          year: int,
          months: [
            { month, revenue_in, expenses_out, net_cf }, ... 12 items
          ],
          total: { revenue_in, expenses_out, net_cf },
          notes: [...]
        }
    """
    # Revenue: received_tnd by paid_at month
    rev_rows = db.query(
        """SELECT EXTRACT(MONTH FROM paid_at)::int AS m,
                  SUM(received_tnd) AS total
             FROM customer_invoices
            WHERE paid_at IS NOT NULL
              AND received_tnd IS NOT NULL
              AND EXTRACT(YEAR FROM paid_at) = %s
            GROUP BY EXTRACT(MONTH FROM paid_at)""",
        (year,),
    )
    rev_by_month = {int(r['m']): float(r['total'] or 0) for r in rev_rows}

    # Expenses: invoices marked paid, anchored by invoice_date (proxy for cash-out month)
    exp_rows = db.query(
        """SELECT EXTRACT(MONTH FROM invoice_date)::int AS m,
                  amount, currency
             FROM invoices
            WHERE invoice_date IS NOT NULL
              AND payment_status = 'paid'
              AND EXTRACT(YEAR FROM invoice_date) = %s""",
        (year,),
    )
    exp_by_month: Dict[int, float] = {}
    for r in exp_rows:
        m = int(r['m'])
        exp_by_month[m] = exp_by_month.get(m, 0.0) + _norm(r['amount'], fx_rates, r.get('currency') or 'TND')

    # Add monthly salary estimate (uniform across elapsed months)
    monthly_salary = _salary_aggregate_tnd(fx_rates, 1)
    today = datetime.utcnow().date()
    last_month_with_salary = today.month if year == today.year else 12

    months_data = []
    for m in range(1, 13):
        rev = rev_by_month.get(m, 0.0)
        exp = exp_by_month.get(m, 0.0)
        if monthly_salary > 0 and m <= last_month_with_salary and year <= today.year:
            exp += monthly_salary
        months_data.append({
            'month':        m,
            'revenue_in':   rev,
            'expenses_out': exp,
            'net_cf':       rev - exp,
        })

    total = {
        'revenue_in':   sum(x['revenue_in']   for x in months_data),
        'expenses_out': sum(x['expenses_out'] for x in months_data),
        'net_cf':       sum(x['net_cf']       for x in months_data),
    }

    notes = ["Inflows = paiements clients encaisses; Outflows = factures fournisseurs payees + salaires."]
    if monthly_salary > 0:
        notes.append("Salaires estimes ajoutes uniformement sur chaque mois ecoule.")

    return {
        'year':   year,
        'months': months_data,
        'total':  total,
        'notes':  notes,
    }


def compute_revenue_breakdown(fx_rates, year: int) -> Dict:
    """Revenue broken down by client, by department, by month."""
    # By client (using amount_tnd accrual basis)
    by_client = db.query(
        """SELECT ci.client_id, c.name AS client_name,
                  SUM(ci.amount_tnd)   AS expected_tnd,
                  SUM(ci.received_tnd) AS received_tnd,
                  COUNT(*)             AS invoice_count
             FROM customer_invoices ci
             LEFT JOIN clients c ON c.id = ci.client_id
            WHERE EXTRACT(YEAR FROM ci.period_month) = %s
            GROUP BY ci.client_id, c.name
            ORDER BY expected_tnd DESC NULLS LAST""",
        (year,),
    )

    by_dept = db.query(
        """SELECT ci.department_id, d.name AS department_name,
                  SUM(ci.amount_tnd)   AS expected_tnd,
                  SUM(ci.received_tnd) AS received_tnd,
                  COUNT(*)             AS invoice_count
             FROM customer_invoices ci
             LEFT JOIN departments d ON d.id = ci.department_id
            WHERE EXTRACT(YEAR FROM ci.period_month) = %s
            GROUP BY ci.department_id, d.name
            ORDER BY expected_tnd DESC NULLS LAST""",
        (year,),
    )

    by_month_rows = db.query(
        """SELECT EXTRACT(MONTH FROM period_month)::int AS m,
                  SUM(amount_tnd)   AS expected_tnd,
                  SUM(received_tnd) AS received_tnd
             FROM customer_invoices
            WHERE EXTRACT(YEAR FROM period_month) = %s
            GROUP BY EXTRACT(MONTH FROM period_month)
            ORDER BY m""",
        (year,),
    )
    by_month = {int(r['m']): r for r in by_month_rows}
    months_series = []
    for m in range(1, 13):
        r = by_month.get(m, {})
        months_series.append({
            'month':        m,
            'expected_tnd': float(r.get('expected_tnd') or 0),
            'received_tnd': float(r.get('received_tnd') or 0),
        })

    return {
        'year': year,
        'by_client': [{
            'client_id':     r.get('client_id'),
            'client_name':   r.get('client_name') or 'Inconnu',
            'expected_tnd':  float(r.get('expected_tnd') or 0),
            'received_tnd':  float(r.get('received_tnd') or 0),
            'invoice_count': int(r.get('invoice_count') or 0),
        } for r in by_client],
        'by_department': [{
            'department_id':   r.get('department_id'),
            'department_name': r.get('department_name') or 'Non assignee',
            'expected_tnd':    float(r.get('expected_tnd') or 0),
            'received_tnd':    float(r.get('received_tnd') or 0),
            'invoice_count':   int(r.get('invoice_count') or 0),
        } for r in by_dept],
        'by_month': months_series,
    }


def compute_expense_breakdown(fx_rates, year: int) -> Dict:
    """Vendor-invoice expenses broken down by category and by month.

    Vendor invoices are converted to TND at current rates (no rate history
    on the invoices table — would require an additional migration).
    """
    rows = db.query(
        """SELECT category, amount, currency, invoice_date
             FROM invoices
            WHERE invoice_date IS NOT NULL
              AND EXTRACT(YEAR FROM invoice_date) = %s""",
        (year,),
    )
    by_cat: Dict[str, dict] = {}
    by_month_total: Dict[int, float] = {}
    grand_total = 0.0
    for r in rows:
        cat = (r.get('category') or 'autre').lower()
        amt = _norm(r.get('amount'), fx_rates, r.get('currency') or 'TND')
        if cat not in by_cat:
            by_cat[cat] = {'category': cat, 'amount_tnd': 0.0, 'count': 0}
        by_cat[cat]['amount_tnd'] += amt
        by_cat[cat]['count']      += 1
        grand_total               += amt

        if r.get('invoice_date'):
            m = r['invoice_date'].month
            by_month_total[m] = by_month_total.get(m, 0.0) + amt

    months_series = [{'month': m, 'amount_tnd': by_month_total.get(m, 0.0)} for m in range(1, 13)]

    return {
        'year':        year,
        'grand_total': grand_total,
        'by_category': sorted(by_cat.values(), key=lambda x: x['amount_tnd'], reverse=True),
        'by_month':    months_series,
    }


def compute_margins(fx_rates, year: int, month: Optional[int] = None) -> Dict:
    """Pull pre-computed P&L and derive margin percentages."""
    pnl = compute_pnl(fx_rates, year, month)
    rev = pnl['revenue']
    t   = pnl['totals']

    def pct(value: float) -> Optional[float]:
        if rev <= 0:
            return None
        return round(100.0 * value / rev, 2)

    gross = rev - t['charges_externes']  # before personnel — proxy for "gross margin"

    return {
        'period':        pnl['period'],
        'revenue_tnd':   rev,
        'gross_profit':  gross,
        'ebitda':        t['ebitda'],
        'operating':     t['operating_income'],
        'net_result':    t['net_result'],
        'margins_pct': {
            'gross':     pct(gross),
            'ebitda':    pct(t['ebitda']),
            'operating': pct(t['operating_income']),
            'net':       pct(t['net_result']),
        },
    }
