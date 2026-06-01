"""
VAT (TVA) declaration engine.

Computes the monthly Tunisian VAT position:
    TVA due = TVA collectée (clients) − TVA déductible (fournisseurs) − Crédit reporté

Where:
- TVA collectée    = sum of vat_amount on customer invoices issued in the month
- TVA déductible   = sum of vat_amount on vendor invoices dated in the month
- Crédit reporté   = positive remainder from the previous month (if any)

The engine returns a structured payload that the UI can render as the
canonical Déclaration TVA mensuelle. It is read-only — no rows written.
"""
from datetime import datetime, date
from typing import Dict, List, Optional
import calendar

import db


def _money(v) -> float:
    try: return float(v or 0)
    except (TypeError, ValueError): return 0.0


# Python's date() only accepts year in [1, 9999]. Clamp upstream input
# so we never crash with "date too large" / "year out of range".
_VAT_YEAR_MIN = 2000
_VAT_YEAR_MAX = 2100


def _safe_year(y) -> int:
    try:
        y = int(y)
    except (TypeError, ValueError):
        return datetime.utcnow().year
    if y < _VAT_YEAR_MIN: return _VAT_YEAR_MIN
    if y > _VAT_YEAR_MAX: return _VAT_YEAR_MAX
    return y


def _safe_month(m) -> int:
    try:
        m = int(m)
    except (TypeError, ValueError):
        return 1
    return 1 if m < 1 else 12 if m > 12 else m


def _month_bounds(year: int, month: int):
    year = _safe_year(year)
    month = _safe_month(month)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _collect_vat_for_month(year: int, month: int) -> Dict:
    """Return TVA collectée + déductible for one month."""
    start, end = _month_bounds(year, month)

    # TVA collectée — issued customer invoices in the period
    coll_rows = db.query(
        """SELECT ci.id, ci.invoice_ref, ci.period_month, ci.issue_date,
                  ci.amount_ht, ci.vat_rate, ci.vat_amount, ci.amount_tnd,
                  c.name AS client_name
             FROM customer_invoices ci
             LEFT JOIN clients c ON c.id = ci.client_id
            WHERE ci.status NOT IN ('cancelled', 'draft')
              AND COALESCE(ci.issue_date, ci.period_month) BETWEEN %s AND %s""",
        (start, end),
    )
    collected = []
    total_ht = 0.0
    total_vat = 0.0
    for r in coll_rows:
        ht = _money(r.get('amount_ht'))
        vat = _money(r.get('vat_amount'))
        # Fallback for legacy rows: if amount_ht is null, treat the amount as TTC, derive HT from vat_rate
        if ht == 0 and _money(r.get('amount_tnd')) > 0:
            rate = _money(r.get('vat_rate'))
            ttc = _money(r.get('amount_tnd'))
            if rate > 0:
                ht = ttc / (1 + rate / 100.0)
                vat = ttc - ht
            else:
                ht = ttc
                vat = 0.0
        total_ht += ht
        total_vat += vat
        collected.append({
            'id':           r['id'],
            'invoice_ref':  r.get('invoice_ref') or '',
            'client_name':  r.get('client_name') or '',
            'issue_date':   str(r.get('issue_date') or ''),
            'amount_ht':    round(ht, 2),
            'vat_rate':     float(r.get('vat_rate') or 0),
            'vat_amount':   round(vat, 2),
        })

    # TVA déductible — vendor invoices dated in the period
    ded_rows = db.query(
        """SELECT i.id, i.invoice_ref, i.invoice_date, i.supplier_name,
                  i.amount_ht, i.vat_rate, i.vat_amount, i.amount_tnd, i.amount, i.currency,
                  v.name AS vendor_name
             FROM invoices i
             LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE i.invoice_date BETWEEN %s AND %s""",
        (start, end),
    )
    deductible = []
    ded_total_ht = 0.0
    ded_total_vat = 0.0
    for r in ded_rows:
        ht = _money(r.get('amount_ht'))
        vat = _money(r.get('vat_amount'))
        # Fallback: derive from amount_tnd or amount + vat_rate
        if ht == 0:
            base = _money(r.get('amount_tnd')) or _money(r.get('amount'))
            rate = _money(r.get('vat_rate'))
            if base > 0:
                if rate > 0:
                    ht = base / (1 + rate / 100.0)
                    vat = base - ht
                else:
                    ht = base
                    vat = 0.0
        ded_total_ht += ht
        ded_total_vat += vat
        deductible.append({
            'id':           r['id'],
            'invoice_ref':  r.get('invoice_ref') or '',
            'supplier':     r.get('vendor_name') or r.get('supplier_name') or '',
            'invoice_date': str(r.get('invoice_date') or ''),
            'amount_ht':    round(ht, 2),
            'vat_rate':     float(r.get('vat_rate') or 0),
            'vat_amount':   round(vat, 2),
        })

    return {
        'collected': {
            'lines':       collected,
            'total_ht':    round(total_ht, 2),
            'total_vat':   round(total_vat, 2),
        },
        'deductible': {
            'lines':       deductible,
            'total_ht':    round(ded_total_ht, 2),
            'total_vat':   round(ded_total_vat, 2),
        },
    }


def declaration_for_month(year: int, month: int,
                          credit_brought_forward: float = 0.0) -> Dict:
    year = _safe_year(year)
    month = _safe_month(month)
    """Compute a single month's VAT declaration.

    Returns:
        {
          period: {year, month, label},
          collected: {lines, total_ht, total_vat},
          deductible: {lines, total_ht, total_vat},
          credit_brought_forward: float (positive = previous month's surplus),
          vat_due:    float (positive = to pay; negative reported as next-month credit),
          vat_to_pay: float (max(0, vat_due)),
          credit_carry_forward: float (max(0, -vat_due)),
        }
    """
    data = _collect_vat_for_month(year, month)
    vat_collected = data['collected']['total_vat']
    vat_deduct    = data['deductible']['total_vat']
    raw_due = vat_collected - vat_deduct - max(0.0, credit_brought_forward)
    month_names_fr = ['', 'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
                      'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']
    return {
        'period': {
            'year':  year,
            'month': month,
            'label': f"{month_names_fr[month]} {year}",
        },
        'collected':              data['collected'],
        'deductible':             data['deductible'],
        'credit_brought_forward': round(credit_brought_forward, 2),
        'vat_due':                round(raw_due, 2),
        'vat_to_pay':             round(max(0.0, raw_due), 2),
        'credit_carry_forward':   round(max(0.0, -raw_due), 2),
    }


def top_contributors(year: int, n: int = 5) -> Dict:
    """Top contributors to VAT collected (clients) and deductible (suppliers)
    for the given year. Returns the top-N by absolute VAT amount.
    """
    year = _safe_year(year)
    start = date(year, 1, 1)
    end   = date(year, 12, 31)

    # Top clients by VAT collected
    clients_rows = db.query(
        """SELECT COALESCE(c.name, 'Inconnu') AS name,
                  SUM(COALESCE(ci.vat_amount, 0)) AS vat_total,
                  SUM(COALESCE(ci.amount_ht, 0))  AS ht_total,
                  COUNT(*)                         AS invoice_count
             FROM customer_invoices ci
             LEFT JOIN clients c ON c.id = ci.client_id
            WHERE ci.status NOT IN ('cancelled', 'draft')
              AND COALESCE(ci.issue_date, ci.period_month) BETWEEN %s AND %s
            GROUP BY c.name
            HAVING SUM(COALESCE(ci.vat_amount, 0)) > 0
            ORDER BY vat_total DESC
            LIMIT %s""",
        (start, end, n),
    )
    top_clients = [{
        'name':          r['name'],
        'vat_amount':    round(float(r['vat_total'] or 0), 2),
        'ht_amount':     round(float(r['ht_total'] or 0), 2),
        'invoice_count': int(r['invoice_count'] or 0),
    } for r in clients_rows]

    # Top suppliers by VAT deductible
    suppliers_rows = db.query(
        """SELECT COALESCE(v.name, i.supplier_name, 'Inconnu') AS name,
                  SUM(COALESCE(i.vat_amount, 0)) AS vat_total,
                  SUM(COALESCE(i.amount_ht, 0))  AS ht_total,
                  COUNT(*)                       AS invoice_count
             FROM invoices i
             LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE i.invoice_date BETWEEN %s AND %s
            GROUP BY COALESCE(v.name, i.supplier_name)
            HAVING SUM(COALESCE(i.vat_amount, 0)) > 0
            ORDER BY vat_total DESC
            LIMIT %s""",
        (start, end, n),
    )
    top_suppliers = [{
        'name':          r['name'],
        'vat_amount':    round(float(r['vat_total'] or 0), 2),
        'ht_amount':     round(float(r['ht_total'] or 0), 2),
        'invoice_count': int(r['invoice_count'] or 0),
    } for r in suppliers_rows]

    return {
        'year':          year,
        'top_clients':   top_clients,
        'top_suppliers': top_suppliers,
    }


def declaration_for_year(year: int) -> Dict:
    year = _safe_year(year)
    """Compute the 12 monthly declarations of a year, with credit-carry-forward
    chained from one month to the next.
    """
    months = []
    credit = 0.0
    for m in range(1, 13):
        decl = declaration_for_month(year, m, credit_brought_forward=credit)
        # The credit_carry_forward of month N is brought forward to month N+1
        credit = decl['credit_carry_forward']
        months.append({
            'month':                  m,
            'label':                  decl['period']['label'],
            'collected_ht':           decl['collected']['total_ht'],
            'collected_vat':          decl['collected']['total_vat'],
            'deductible_ht':          decl['deductible']['total_ht'],
            'deductible_vat':         decl['deductible']['total_vat'],
            'credit_brought_forward': decl['credit_brought_forward'],
            'vat_due':                decl['vat_due'],
            'vat_to_pay':             decl['vat_to_pay'],
            'credit_carry_forward':   decl['credit_carry_forward'],
        })
    year_total = {
        'collected_vat':  sum(m['collected_vat']  for m in months),
        'deductible_vat': sum(m['deductible_vat'] for m in months),
        'vat_paid_ytd':   sum(m['vat_to_pay']     for m in months),
        'credit_carry_forward': months[-1]['credit_carry_forward'] if months else 0,
    }
    return {'year': year, 'months': months, 'year_total': year_total}
