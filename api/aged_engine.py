"""
Aged receivables & payables engine.

For each unpaid (or partially paid) invoice — vendor side and customer
side — bucket the outstanding amount by age relative to the due_date:

    Bucket  Days past due
    -----------------------------------
    current   <=0   (not yet due)
    30        1-30  (less than a month overdue)
    60        31-60
    90        61-90
    120+      >90   (severely overdue)

Returns totals per bucket, per counterparty (vendor or client), and
the raw line items for drill-down.
"""
from datetime import datetime, date
from typing import Dict, List

import db


BUCKETS = [
    ('current', 'À échoir / pas encore dû', 0, 0),
    ('b30',     '1 à 30 jours',             1, 30),
    ('b60',     '31 à 60 jours',            31, 60),
    ('b90',     '61 à 90 jours',            61, 90),
    ('b120',    'Plus de 90 jours',         91, 99999),
]


def _bucket_for_days(days: int) -> str:
    """Return the bucket id given the number of days past due (negative = not due yet)."""
    if days <= 0:
        return 'current'
    if days <= 30:
        return 'b30'
    if days <= 60:
        return 'b60'
    if days <= 90:
        return 'b90'
    return 'b120'


def _money(v) -> float:
    try: return float(v or 0)
    except (TypeError, ValueError): return 0.0


def _empty_buckets() -> Dict[str, float]:
    return {b[0]: 0.0 for b in BUCKETS}


def aged_receivables() -> Dict:
    """Aged customer-invoice receivables (montants TND restants à encaisser)."""
    today = datetime.utcnow().date()
    rows = db.query(
        """SELECT ci.id, ci.invoice_ref, ci.due_date, ci.amount_tnd, ci.received_tnd,
                  ci.status, ci.client_id,
                  c.name AS client_name,
                  COALESCE(SUM(p.amount_tnd), 0) AS paid_sum
             FROM customer_invoices ci
             LEFT JOIN clients c ON c.id = ci.client_id
             LEFT JOIN invoice_payments p ON p.customer_invoice_id = ci.id
            WHERE ci.status NOT IN ('paid', 'cancelled')
              AND ci.amount_tnd IS NOT NULL
            GROUP BY ci.id, c.name"""
    )

    lines = []
    by_client: Dict[str, Dict] = {}
    bucket_totals = _empty_buckets()

    for r in rows:
        amt    = _money(r.get('amount_tnd'))
        # Outstanding = expected - max(received_tnd, paid_sum)
        received = max(_money(r.get('received_tnd')), _money(r.get('paid_sum')))
        outstanding = max(0.0, amt - received)
        if outstanding < 0.01:
            continue
        due = r.get('due_date')
        days_overdue = (today - due).days if due else 0
        bucket = _bucket_for_days(days_overdue)
        bucket_totals[bucket] += outstanding

        client_name = r.get('client_name') or 'Inconnu'
        client_key  = r.get('client_id') or client_name
        if client_key not in by_client:
            by_client[client_key] = {
                'client_id':   r.get('client_id'),
                'client_name': client_name,
                'buckets':     _empty_buckets(),
                'total':       0.0,
            }
        by_client[client_key]['buckets'][bucket] += outstanding
        by_client[client_key]['total']           += outstanding

        lines.append({
            'id':            r['id'],
            'invoice_ref':   r.get('invoice_ref') or '',
            'client_name':   client_name,
            'due_date':      str(due) if due else '',
            'days_overdue':  days_overdue,
            'outstanding':   round(outstanding, 2),
            'bucket':        bucket,
        })

    return {
        'as_of':         today.isoformat(),
        'buckets':       {b[0]: {'label': b[1], 'total': round(bucket_totals[b[0]], 2)} for b in BUCKETS},
        'grand_total':   round(sum(bucket_totals.values()), 2),
        'by_client':     [{**v,
                           'buckets': {k: round(x, 2) for k, x in v['buckets'].items()},
                           'total':   round(v['total'], 2)}
                          for v in sorted(by_client.values(), key=lambda x: -x['total'])],
        'lines':         sorted(lines, key=lambda x: -x['days_overdue']),
    }


def aged_payables(fx_rates=None) -> Dict:
    """Aged vendor-invoice payables (montants TND restants à payer)."""
    today = datetime.utcnow().date()
    rows = db.query(
        """SELECT i.id, i.invoice_ref, i.due_date, i.amount, i.amount_tnd, i.currency,
                  i.payment_status, i.vendor_id, i.supplier_name,
                  v.name AS vendor_name,
                  COALESCE(SUM(p.amount_tnd), 0) AS paid_sum
             FROM invoices i
             LEFT JOIN vendors v ON v.id = i.vendor_id
             LEFT JOIN invoice_payments p ON p.invoice_id = i.id
            WHERE i.payment_status IN ('unpaid', 'overdue')
            GROUP BY i.id, v.name"""
    )

    lines = []
    by_vendor: Dict[str, Dict] = {}
    bucket_totals = _empty_buckets()

    for r in rows:
        # Amount in TND — prefer stored, fall back to live conversion
        amt_tnd = _money(r.get('amount_tnd'))
        if amt_tnd == 0 and fx_rates:
            amt_tnd = fx_rates.to_tnd(_money(r.get('amount')), r.get('currency') or 'TND')
        elif amt_tnd == 0:
            amt_tnd = _money(r.get('amount'))
        paid = _money(r.get('paid_sum'))
        outstanding = max(0.0, amt_tnd - paid)
        if outstanding < 0.01:
            continue
        due = r.get('due_date')
        days_overdue = (today - due).days if due else 0
        bucket = _bucket_for_days(days_overdue)
        bucket_totals[bucket] += outstanding

        name = r.get('vendor_name') or r.get('supplier_name') or 'Inconnu'
        key  = r.get('vendor_id') or name
        if key not in by_vendor:
            by_vendor[key] = {
                'vendor_id':   r.get('vendor_id'),
                'vendor_name': name,
                'buckets':     _empty_buckets(),
                'total':       0.0,
            }
        by_vendor[key]['buckets'][bucket] += outstanding
        by_vendor[key]['total']           += outstanding

        lines.append({
            'id':            r['id'],
            'invoice_ref':   r.get('invoice_ref') or '',
            'vendor_name':   name,
            'due_date':      str(due) if due else '',
            'days_overdue':  days_overdue,
            'outstanding':   round(outstanding, 2),
            'bucket':        bucket,
        })

    return {
        'as_of':         today.isoformat(),
        'buckets':       {b[0]: {'label': b[1], 'total': round(bucket_totals[b[0]], 2)} for b in BUCKETS},
        'grand_total':   round(sum(bucket_totals.values()), 2),
        'by_vendor':     [{**v,
                           'buckets': {k: round(x, 2) for k, x in v['buckets'].items()},
                           'total':   round(v['total'], 2)}
                          for v in sorted(by_vendor.values(), key=lambda x: -x['total'])],
        'lines':         sorted(lines, key=lambda x: -x['days_overdue']),
    }
