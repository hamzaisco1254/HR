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
from planned_expense_store import PlannedExpenseStore, get_finance_rates


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

def _period_bounds(year: int, month: Optional[int]) -> tuple:
    """Return (start_date, end_date) inclusive for the requested period."""
    import calendar
    if month:
        last = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last)
    return date(year, 1, 1), date(year, 12, 31)


def compute_pnl(fx_rates, year: int, month: Optional[int] = None, basis: str = 'accrual',
                include_planned: bool = True) -> Dict:
    """French-style P&L for the period.

    basis='accrual': revenue = sum of customer_invoices.amount_tnd
    basis='cash':    revenue = sum of customer_invoices.received_tnd

    The cascade now includes three accrual lines on top of recorded invoices:
      - planned expenses falling in the period (only those NOT yet materialized
        into a real invoice, to avoid double-counting)
      - CNSS employer contribution = personnel_base * cnss_employer_rate%
      - Corporate tax provision    = max(0, operating_income) * corporate_tax_rate%

    Returns:
        {
          period: {year, month, basis, months_covered, rates: {...}},
          revenue: float,
          expenses_by_bucket: { bucket_id: {label, amount, items: [...]} },
          totals: {
            charges_externes, charges_personnel, cnss_employer, taxes,
            charges_financieres, planned_extra_charges_externes,
            total_expenses, ebitda, operating_income,
            net_before_tax, corporate_tax_provision, net_result
          },
          planned: {total, by_category},
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

    # Bucket vendor invoices by category
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
            'source': 'invoice',
        })

    # Add salary aggregate to charges_personnel
    if salary_est_tnd > 0:
        buckets['charges_personnel']['amount'] += salary_est_tnd
        buckets['charges_personnel']['items'].append({
            'category': 'salaires_estimes',
            'amount_tnd': salary_est_tnd,
            'supplier_name': f'Salaires bruts (estimes, {months_covered} mois)',
            'invoice_date': '',
            'source': 'salary_estimate',
        })

    # ─── Planned expenses (uninvoiced commitments) ──────────────
    planned_total = 0.0
    planned_by_cat: Dict[str, float] = {}
    if include_planned:
        period_start, period_end = _period_bounds(year, month)
        pes = PlannedExpenseStore(fx_rates=fx_rates)
        for occ in pes.occurrences(period_start, period_end, only_pending=True):
            cat    = (occ.get('category') or 'autre').lower()
            bucket = _CATEGORY_BUCKETS.get(cat, 'charges_externes')
            amt    = float(occ.get('amount_tnd') or 0)
            buckets[bucket]['amount'] += amt
            buckets[bucket]['items'].append({
                'category': cat,
                'amount_tnd': amt,
                'supplier_name': occ.get('name') or 'Depense prevue',
                'invoice_date': occ.get('occurrence_date') or '',
                'source': 'planned',
            })
            planned_total += amt
            planned_by_cat[cat] = planned_by_cat.get(cat, 0.0) + amt

    # ─── CNSS employer contribution ─────────────────────────────
    rates = get_finance_rates()
    cnss_rate = float(rates.get('cnss_employer_rate', 0) or 0) / 100.0
    # Base: salaire estime + salaires invoices (categories='salaires')
    personnel_for_cnss = salary_est_tnd
    for item in buckets['charges_personnel']['items']:
        if item.get('category') in ('salaires',):
            personnel_for_cnss += item['amount_tnd']
    cnss_employer = max(0.0, personnel_for_cnss * cnss_rate)
    if cnss_employer > 0:
        buckets['charges_personnel']['amount'] += cnss_employer
        buckets['charges_personnel']['items'].append({
            'category': 'cnss_employer',
            'amount_tnd': cnss_employer,
            'supplier_name': f'Cotisations CNSS employeur ({rates.get("cnss_employer_rate", 0):.2f}%)',
            'invoice_date': '',
            'source': 'cnss_provision',
        })

    charges_externes      = buckets['charges_externes']['amount']
    charges_personnel     = buckets['charges_personnel']['amount']
    taxes_amt             = buckets['taxes']['amount']
    charges_financieres   = buckets['charges_financieres']['amount']

    # French P&L cascade
    ebitda           = revenue - charges_externes - charges_personnel
    operating_income = ebitda  # no D&A modeled
    net_before_tax   = operating_income - charges_financieres - taxes_amt

    # Corporate tax provision on positive pre-tax result only
    is_rate = float(rates.get('corporate_tax_rate', 0) or 0) / 100.0
    corporate_tax_provision = max(0.0, net_before_tax) * is_rate
    net_result = net_before_tax - corporate_tax_provision

    total_expenses = (charges_externes + charges_personnel + taxes_amt
                      + charges_financieres + corporate_tax_provision)

    notes = []
    if any(_norm(r.get('amount'), fx_rates, r.get('currency') or 'TND') != float(r.get('amount') or 0)
           for r in exp_rows):
        notes.append("Les depenses en devises etrangeres sont converties au taux actuel (non historise).")
    if salary_est_tnd > 0:
        notes.append(f"Les salaires sont estimes a partir des fiches employes ({months_covered} mois).")
    if cnss_employer > 0:
        notes.append(f"Cotisation patronale CNSS calculee a {rates.get('cnss_employer_rate', 0):.2f}% de la masse salariale brute.")
    if planned_total > 0:
        notes.append(f"Inclut {planned_total:,.0f} TND de depenses prevues (non encore facturees).".replace(',', ' '))
    if corporate_tax_provision > 0:
        notes.append(f"Provision IS calculee a {rates.get('corporate_tax_rate', 0):.2f}% du resultat avant impot.")
    notes.append("Aucune dotation aux amortissements modelisee (resultat operationnel = EBITDA).")

    return {
        'period': {
            'year': year, 'month': month, 'basis': basis,
            'months_covered': months_covered,
            'rates': rates,
        },
        'revenue': revenue,
        'expenses_by_bucket': buckets,
        'totals': {
            'charges_externes':         charges_externes,
            'charges_personnel':        charges_personnel,
            'cnss_employer':            cnss_employer,
            'taxes':                    taxes_amt,
            'charges_financieres':      charges_financieres,
            'planned_included':         planned_total,
            'total_expenses':           total_expenses,
            'ebitda':                   ebitda,
            'operating_income':         operating_income,
            'net_before_tax':           net_before_tax,
            'corporate_tax_provision':  corporate_tax_provision,
            'net_result':               net_result,
        },
        'planned': {
            'total':       planned_total,
            'by_category': planned_by_cat,
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

    # Planned-expense outflows by month (uninvoiced commitments)
    pes = PlannedExpenseStore(fx_rates=fx_rates)
    planned_by_month: Dict[int, float] = {}
    period_start = date(year, 1, 1)
    period_end   = date(year, 12, 31)
    for occ in pes.occurrences(period_start, period_end, only_pending=True):
        d = datetime.strptime(occ['occurrence_date'], '%Y-%m-%d').date()
        planned_by_month[d.month] = planned_by_month.get(d.month, 0.0) + occ['amount_tnd']
    # Fold planned outflows into expenses_out + re-derive net_cf
    for m in months_data:
        extra = planned_by_month.get(m['month'], 0.0)
        if extra:
            m['expenses_out'] += extra
            m['net_cf']        = m['revenue_in'] - m['expenses_out']

    total = {
        'revenue_in':   sum(x['revenue_in']   for x in months_data),
        'expenses_out': sum(x['expenses_out'] for x in months_data),
        'net_cf':       sum(x['net_cf']       for x in months_data),
    }

    notes = ["Inflows = paiements clients encaisses; Outflows = factures fournisseurs payees + salaires + depenses prevues."]
    if monthly_salary > 0:
        notes.append("Salaires estimes ajoutes uniformement sur chaque mois ecoule.")
    if any(planned_by_month.values()):
        notes.append("Depenses prevues (non facturees) incluses dans les sorties.")

    return {
        'year':   year,
        'months': months_data,
        'total':  total,
        'notes':  notes,
    }


def compute_planned_variance(fx_rates, year: int) -> Dict:
    """Planned vs actual by month per category — the variance table.

    Actual = vendor invoices (TND-normalized) for that month/category.
    Planned = planned_expenses occurrences for that month/category
              (regardless of pending/paid status — we want to compare).
    """
    # Pull all planned expenses (including paid ones, to compare against actual)
    pes = PlannedExpenseStore(fx_rates=fx_rates)
    period_start = date(year, 1, 1)
    period_end   = date(year, 12, 31)
    planned: Dict[int, Dict[str, float]] = {m: {} for m in range(1, 13)}
    for occ in pes.occurrences(period_start, period_end, only_pending=False):
        d = datetime.strptime(occ['occurrence_date'], '%Y-%m-%d').date()
        cat = occ['category']
        planned[d.month].setdefault(cat, 0.0)
        planned[d.month][cat] += occ['amount_tnd']

    # Pull actual invoices for the year
    actual: Dict[int, Dict[str, float]] = {m: {} for m in range(1, 13)}
    rows = db.query(
        """SELECT invoice_date, amount, currency, category
             FROM invoices
            WHERE invoice_date IS NOT NULL
              AND EXTRACT(YEAR FROM invoice_date) = %s""",
        (year,),
    )
    for r in rows:
        m   = r['invoice_date'].month
        cat = (r.get('category') or 'autre').lower()
        amt = _norm(r.get('amount'), fx_rates, r.get('currency') or 'TND')
        actual[m].setdefault(cat, 0.0)
        actual[m][cat] += amt

    # Build the response
    all_cats = set()
    for m in range(1, 13):
        all_cats.update(planned[m].keys())
        all_cats.update(actual[m].keys())
    cats_sorted = sorted(all_cats)

    months_out = []
    totals_planned = 0.0
    totals_actual  = 0.0
    for m in range(1, 13):
        cats_row = []
        m_planned = 0.0
        m_actual  = 0.0
        for c in cats_sorted:
            p = planned[m].get(c, 0.0)
            a = actual[m].get(c, 0.0)
            cats_row.append({
                'category': c,
                'planned':  p,
                'actual':   a,
                'delta':    a - p,
            })
            m_planned += p
            m_actual  += a
        months_out.append({
            'month':         m,
            'categories':    cats_row,
            'total_planned': m_planned,
            'total_actual':  m_actual,
            'delta':         m_actual - m_planned,
        })
        totals_planned += m_planned
        totals_actual  += m_actual

    return {
        'year':         year,
        'categories':   cats_sorted,
        'months':       months_out,
        'year_total': {
            'planned': totals_planned,
            'actual':  totals_actual,
            'delta':   totals_actual - totals_planned,
        },
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
