"""
Invoice payments — partial/multi-payment tracking.

A single invoice (either a vendor invoice or a customer invoice) can
have multiple payment rows. The aggregate TND amount paid is computed
from this table; the parent invoice's payment_status / received_tnd
fields are refreshed accordingly to keep the rest of the system in sync.

Each row links to exactly ONE of:
  - invoices.id           (vendor side, money going OUT)
  - customer_invoices.id  (revenue side, money coming IN)

The CHECK constraint in the schema enforces this exclusivity.
"""
import uuid
from datetime import datetime
from typing import Optional, Dict, List

import db


VALID_METHODS = ('virement', 'cheque', 'especes', 'carte', 'autre')
VALID_CURRENCIES = ('TND', 'EUR', 'USD', 'GBP', 'CHF')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _norm_method(value: str) -> Optional[str]:
    v = (value or '').strip().lower()
    return v if v in VALID_METHODS else None


def _norm_currency(value: str) -> str:
    code = (value or '').strip().upper()
    return code if code in VALID_CURRENCIES else 'TND'


def _row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    def _dt(v):
        if v is None: return ''
        if hasattr(v, 'isoformat'): return v.isoformat()
        return str(v)
    return {
        'id':                   r['id'],
        'invoice_id':           r.get('invoice_id'),
        'customer_invoice_id':  r.get('customer_invoice_id'),
        'payment_date':         _dt(r.get('payment_date')),
        'amount':               float(r.get('amount') or 0),
        'currency':             r.get('currency') or 'TND',
        'fx_rate':              float(r['fx_rate']) if r.get('fx_rate') is not None else None,
        'amount_tnd':           float(r.get('amount_tnd') or 0),
        'method':               r.get('method') or '',
        'reference':            r.get('reference') or '',
        'account_id':           r.get('account_id'),
        'notes':                r.get('notes') or '',
        'created_at':           _dt(r.get('created_at')),
    }


class InvoicePaymentStore:
    def __init__(self, fx_rates=None):
        self.fx = fx_rates

    # ── Aggregate helpers ──────────────────────────────────────

    def total_paid_for_invoice(self, invoice_id: str) -> float:
        if not invoice_id: return 0.0
        row = db.one(
            "SELECT COALESCE(SUM(amount_tnd), 0) AS sum FROM invoice_payments WHERE invoice_id = %s",
            (invoice_id,),
        )
        return float(row.get('sum') or 0) if row else 0.0

    def total_received_for_customer_invoice(self, customer_invoice_id: str) -> float:
        if not customer_invoice_id: return 0.0
        row = db.one(
            "SELECT COALESCE(SUM(amount_tnd), 0) AS sum FROM invoice_payments WHERE customer_invoice_id = %s",
            (customer_invoice_id,),
        )
        return float(row.get('sum') or 0) if row else 0.0

    # ── CRUD ───────────────────────────────────────────────────

    def list_for_invoice(self, invoice_id: str) -> List[Dict]:
        rows = db.query(
            "SELECT * FROM invoice_payments WHERE invoice_id = %s ORDER BY payment_date DESC",
            (invoice_id,),
        )
        return [_row(r) for r in rows]

    def list_for_customer_invoice(self, customer_invoice_id: str) -> List[Dict]:
        rows = db.query(
            "SELECT * FROM invoice_payments WHERE customer_invoice_id = %s ORDER BY payment_date DESC",
            (customer_invoice_id,),
        )
        return [_row(r) for r in rows]

    def add(self, data: Dict, created_by: Optional[str] = None) -> Dict:
        invoice_id          = (data.get('invoice_id') or '').strip() or None
        customer_invoice_id = (data.get('customer_invoice_id') or '').strip() or None
        if bool(invoice_id) == bool(customer_invoice_id):
            raise ValueError("Préciser exactement une cible : invoice_id OU customer_invoice_id.")
        if invoice_id and not db.one("SELECT 1 FROM invoices WHERE id = %s", (invoice_id,)):
            raise ValueError("Facture fournisseur introuvable.")
        if customer_invoice_id and not db.one("SELECT 1 FROM customer_invoices WHERE id = %s", (customer_invoice_id,)):
            raise ValueError("Facture client introuvable.")

        try:
            amount = float(data.get('amount') or 0)
        except (TypeError, ValueError):
            raise ValueError("Montant invalide.")
        if amount <= 0:
            raise ValueError("Le montant doit être strictement positif.")

        currency = _norm_currency(data.get('currency', 'TND'))
        # Compute TND amount
        if currency == 'TND':
            fx_rate = None
            amount_tnd = amount
        else:
            try:
                fx_rate = float(data.get('fx_rate')) if data.get('fx_rate') not in (None, '') else None
            except (TypeError, ValueError):
                fx_rate = None
            if fx_rate is None and self.fx:
                fx_rate = self.fx.get_rate(currency, 'TND')
                if fx_rate is not None and fx_rate <= 0:
                    fx_rate = None
            amount_tnd = round(amount * fx_rate, 2) if fx_rate else amount

        payment_date = data.get('payment_date')
        if not payment_date:
            raise ValueError("Date du paiement requise.")

        pid = _new_id()
        db.execute(
            """INSERT INTO invoice_payments (
                 id, invoice_id, customer_invoice_id, payment_date,
                 amount, currency, fx_rate, amount_tnd,
                 method, reference, account_id, notes,
                 created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (pid, invoice_id, customer_invoice_id, payment_date,
             amount, currency, fx_rate, amount_tnd,
             _norm_method(data.get('method')),
             (data.get('reference') or '').strip() or None,
             (data.get('account_id') or None),
             (data.get('notes') or '').strip() or None,
             created_by, datetime.utcnow()),
        )

        # Refresh the parent invoice's derived state
        if invoice_id:
            self._refresh_invoice_status(invoice_id)
        else:
            self._refresh_customer_invoice_status(customer_invoice_id)
        return _row(db.one("SELECT * FROM invoice_payments WHERE id = %s", (pid,)))

    def delete(self, pid: str) -> bool:
        row = db.one("SELECT invoice_id, customer_invoice_id FROM invoice_payments WHERE id = %s", (pid,))
        if not row: return False
        ok = db.execute("DELETE FROM invoice_payments WHERE id = %s", (pid,)) > 0
        if ok:
            if row.get('invoice_id'):
                self._refresh_invoice_status(row['invoice_id'])
            elif row.get('customer_invoice_id'):
                self._refresh_customer_invoice_status(row['customer_invoice_id'])
        return ok

    # ── Status refresh helpers ─────────────────────────────────

    def _refresh_invoice_status(self, invoice_id: str) -> None:
        """Recompute payment_status + paid_at on the vendor invoice."""
        inv = db.one("SELECT amount_tnd, amount, due_date FROM invoices WHERE id = %s", (invoice_id,))
        if not inv: return
        paid = self.total_paid_for_invoice(invoice_id)
        target = float(inv.get('amount_tnd') or inv.get('amount') or 0)
        today = datetime.utcnow().date()
        due_date = inv.get('due_date')

        if target <= 0:
            new_status = 'unpaid'
            paid_at = None
        elif paid >= target - 0.01:
            new_status = 'paid'
            # Use the latest payment date as paid_at
            r = db.one(
                "SELECT MAX(payment_date) AS d FROM invoice_payments WHERE invoice_id = %s",
                (invoice_id,),
            )
            paid_at = r.get('d') if r else today
        elif due_date and due_date < today:
            new_status = 'overdue'
            paid_at = None
        else:
            new_status = 'unpaid'
            paid_at = None
        db.execute(
            "UPDATE invoices SET payment_status = %s, paid_at = %s, updated_at = %s WHERE id = %s",
            (new_status, paid_at, datetime.utcnow(), invoice_id),
        )

    def _refresh_customer_invoice_status(self, ciid: str) -> None:
        """Recompute received_tnd / status / paid_at on the customer invoice."""
        inv = db.one("SELECT amount_tnd, due_date, status FROM customer_invoices WHERE id = %s", (ciid,))
        if not inv: return
        received = self.total_received_for_customer_invoice(ciid)
        target = float(inv.get('amount_tnd') or 0)
        today = datetime.utcnow().date()
        due_date = inv.get('due_date')

        if target > 0 and received >= target - 0.01:
            new_status = 'paid'
            r = db.one(
                "SELECT MAX(payment_date) AS d FROM invoice_payments WHERE customer_invoice_id = %s",
                (ciid,),
            )
            paid_at = r.get('d') if r else today
        elif due_date and due_date < today and (inv.get('status') or 'draft') != 'cancelled':
            new_status = 'overdue'
            paid_at = None
        else:
            # Keep manual draft/sent state if no payment yet, otherwise mark sent
            current_status = inv.get('status') or 'draft'
            if received > 0 and current_status in ('draft',):
                new_status = 'sent'
            else:
                new_status = current_status
            paid_at = None
        db.execute(
            """UPDATE customer_invoices
                  SET received_tnd = %s, status = %s, paid_at = %s, updated_at = %s
                WHERE id = %s""",
            (received, new_status, paid_at, datetime.utcnow(), ciid),
        )


# ─── Planned expense auto-match helper ───────────────────────────

def auto_match_planned_expense(invoice: Dict, fx_rates=None) -> Optional[str]:
    """When a vendor invoice is created, look for a planned_expense that
    matches (same vendor name OR supplier_name OR category, similar amount,
    same month) and mark it as paid + linked to the invoice. Returns the
    matched planned_expense id, or None.

    Heuristic: amount within ±5%, same calendar month as invoice_date,
    not already materialized, category match preferred.
    """
    if not invoice: return None
    inv_date = invoice.get('invoice_date')
    if not inv_date:
        return None
    try:
        if isinstance(inv_date, str):
            from datetime import date
            inv_date = date.fromisoformat(inv_date[:10])
    except (ValueError, TypeError):
        return None

    target = float(invoice.get('amount_tnd') or invoice.get('amount') or 0)
    if target <= 0: return None
    low, high = target * 0.95, target * 1.05
    category = (invoice.get('category') or '').lower()

    # Search candidates: status='planned', linked_invoice_id IS NULL, occurrence in inv_date's month
    candidates = db.query("""
        SELECT id, name, category, amount_tnd, is_recurring, frequency,
               start_date, end_date, due_date
          FROM planned_expenses
         WHERE status = 'planned'
           AND linked_invoice_id IS NULL
           AND amount_tnd BETWEEN %s AND %s
    """, (low, high))

    best = None
    for c in candidates:
        # Check month proximity
        cand_dates = []
        if not c.get('is_recurring') and c.get('due_date'):
            cand_dates.append(c['due_date'])
        elif c.get('is_recurring') and c.get('start_date'):
            # Quick check: is inv_date between start and end (or no end)?
            sd = c['start_date']
            ed = c.get('end_date')
            if sd <= inv_date and (ed is None or inv_date <= ed):
                cand_dates.append(inv_date)

        for cd in cand_dates:
            if cd.year == inv_date.year and cd.month == inv_date.month:
                # Strong category match preferred
                cat_match = (category and c.get('category', '').lower() == category)
                score = 2 if cat_match else 1
                if best is None or score > best[0]:
                    best = (score, c['id'])
                break

    if not best:
        return None
    matched_id = best[1]
    # Mark planned as paid + linked
    db.execute(
        """UPDATE planned_expenses
              SET status = 'paid', linked_invoice_id = %s, updated_at = %s
            WHERE id = %s""",
        (invoice['id'], datetime.utcnow(), matched_id),
    )
    return matched_id
