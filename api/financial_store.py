"""
Financial data persistence layer — Postgres-backed.

Handles storage for invoices, financial balances, payments.
Exchange rates stored in kv_settings table.
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import db


# ═══════════════════════════════════════════════════════════════════
# Invoice Store
# ═══════════════════════════════════════════════════════════════════

class InvoiceStore:
    """CRUD for invoices in Postgres."""

    def add_invoice(self, data: Dict) -> Dict:
        inv_id = str(uuid.uuid4())
        now = datetime.utcnow()
        try:
            amount = float(data.get('amount', 0))
        except (ValueError, TypeError):
            amount = 0.0
        db.execute(
            """INSERT INTO invoices (id, supplier_name, invoice_ref, invoice_date,
               due_date, amount, currency, payment_status, notes, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                inv_id,
                data.get('supplier_name', ''),
                data.get('invoice_ref', ''),
                data.get('invoice_date') or None,   # '' → NULL for DATE
                data.get('due_date') or None,        # '' → NULL for DATE
                amount,
                data.get('currency', 'TND'),
                data.get('payment_status', 'unpaid'),
                data.get('notes', ''),
                now, now,
            ),
        )
        return self.get_by_id(inv_id) or {'id': inv_id}

    def get_all(self, filters: Optional[Dict] = None) -> List[Dict]:
        sql = "SELECT * FROM invoices"
        params: list = []
        clauses: list = []

        if filters:
            if filters.get('status'):
                clauses.append("payment_status = %s"); params.append(filters['status'])
            if filters.get('currency'):
                clauses.append("currency = %s"); params.append(filters['currency'])
            if filters.get('supplier'):
                clauses.append("supplier_name ILIKE %s"); params.append(f"%{filters['supplier']}%")
            if filters.get('date_from'):
                clauses.append("invoice_date >= %s"); params.append(filters['date_from'])
            if filters.get('date_to'):
                clauses.append("invoice_date <= %s"); params.append(filters['date_to'])

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        return [self._row(r) for r in db.query(sql, tuple(params))]

    def get_by_id(self, invoice_id: str) -> Optional[Dict]:
        row = db.one("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        return self._row(row) if row else None

    def update_invoice(self, invoice_id: str, updates: Dict) -> Optional[Dict]:
        allowed = ('supplier_name', 'invoice_ref', 'invoice_date', 'due_date',
                    'amount', 'currency', 'payment_status', 'notes')
        # Fields that are DATE type in Postgres — empty string must become NULL
        date_fields = ('invoice_date', 'due_date')
        sets = []
        params = []
        for k, v in updates.items():
            if k in allowed:
                if k in date_fields:
                    v = v if v else None  # '' → NULL for DATE columns
                if k == 'amount':
                    try: v = float(v)
                    except (ValueError, TypeError): v = 0.0
                sets.append(f"{k} = %s")
                params.append(v)
        if not sets:
            return self.get_by_id(invoice_id)
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(invoice_id)
        db.execute(f"UPDATE invoices SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get_by_id(invoice_id)

    def delete_invoice(self, invoice_id: str) -> bool:
        return db.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,)) > 0

    def total_by_status(self, currency: str = None) -> Dict:
        rows = self.get_all({'currency': currency} if currency else None)
        paid = unpaid = overdue = 0.0
        for inv in rows:
            amt = float(inv.get('amount', 0))
            s   = inv.get('payment_status', 'unpaid')
            if s == 'paid':       paid += amt
            elif s == 'overdue':  overdue += amt
            else:                 unpaid += amt
        return {'paid': paid, 'unpaid': unpaid, 'overdue': overdue}

    def total_by_supplier(self) -> Dict[str, float]:
        rows = db.query(
            "SELECT supplier_name, SUM(amount) AS total FROM invoices GROUP BY supplier_name ORDER BY total DESC"
        )
        return {r['supplier_name'] or 'Inconnu': float(r['total'] or 0) for r in rows}

    def get_audit_trail(self, entity_id: str = None) -> List[Dict]:
        if entity_id:
            return db.query(
                "SELECT * FROM audit_log WHERE entity_type = 'invoice' AND entity_id = %s ORDER BY ts DESC",
                (entity_id,),
            )
        return db.query(
            "SELECT * FROM audit_log WHERE entity_type = 'invoice' ORDER BY ts DESC LIMIT 200"
        )

    @staticmethod
    def _row(r: dict) -> dict:
        if not r:
            return {}
        def _dt(v):
            """Safely convert date/datetime to ISO string."""
            if v is None:
                return ''
            if hasattr(v, 'isoformat'):
                return v.isoformat()
            return str(v)
        return {
            'id':              r.get('id', ''),
            'supplier_name':   r.get('supplier_name', ''),
            'invoice_ref':     r.get('invoice_ref', ''),
            'invoice_date':    _dt(r.get('invoice_date')),
            'due_date':        _dt(r.get('due_date')),
            'amount':          float(r.get('amount', 0)),
            'currency':        r.get('currency', 'TND'),
            'payment_status':  r.get('payment_status', 'unpaid'),
            'notes':           r.get('notes', ''),
            'created_at':      _dt(r.get('created_at')),
            'updated_at':      _dt(r.get('updated_at')),
        }


# ═══════════════════════════════════════════════════════════════════
# Balance Store
# ═══════════════════════════════════════════════════════════════════

class BalanceStore:
    """CRUD for bank accounts and cash balances."""

    def __init__(self):
        # Seed default accounts if table is empty
        cnt = (db.one("SELECT COUNT(*) AS cnt FROM balances") or {}).get('cnt', 0)
        if cnt == 0:
            defaults = [
                ('Compte Bancaire TND', 'TND', 0.0),
                ('Compte Bancaire EUR', 'EUR', 0.0),
                ('Caisse (Espèces)',    'TND', 0.0),
            ]
            for name, cur, bal in defaults:
                db.execute(
                    "INSERT INTO balances (id, account_name, balance, currency, last_updated) VALUES (%s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), name, bal, cur, datetime.utcnow()),
                )

    def get_all(self) -> List[Dict]:
        rows = db.query("SELECT * FROM balances ORDER BY account_name")
        return [self._row(r) for r in rows]

    def update_balance(self, account_id: str, balance: float) -> Optional[Dict]:
        cnt = db.execute(
            "UPDATE balances SET balance = %s, last_updated = %s WHERE id = %s",
            (balance, datetime.utcnow(), account_id),
        )
        if cnt == 0:
            return None
        row = db.one("SELECT * FROM balances WHERE id = %s", (account_id,))
        return self._row(row) if row else None

    def add_account(self, name: str, acc_type: str, currency: str, balance: float = 0.0) -> Dict:
        acc_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO balances (id, account_name, balance, currency, last_updated) VALUES (%s, %s, %s, %s, %s)",
            (acc_id, name, balance, currency, datetime.utcnow()),
        )
        return self._row(db.one("SELECT * FROM balances WHERE id = %s", (acc_id,)) or {})

    def delete_account(self, account_id: str) -> bool:
        return db.execute("DELETE FROM balances WHERE id = %s", (account_id,)) > 0

    def total_by_currency(self) -> Dict[str, float]:
        rows = db.query("SELECT currency, SUM(balance) AS total FROM balances GROUP BY currency")
        return {r['currency']: float(r['total'] or 0) for r in rows}

    @staticmethod
    def _row(r: dict) -> dict:
        if not r:
            return {}
        lu = r.get('last_updated')
        return {
            'id':         r.get('id', ''),
            'name':       r.get('account_name', ''),
            'type':       'bank',
            'currency':   r.get('currency', 'TND'),
            'balance':    float(r.get('balance', 0)),
            'updated_at': lu.isoformat() if hasattr(lu, 'isoformat') else str(lu or ''),
        }


# ═══════════════════════════════════════════════════════════════════
# Payment Store
# ═══════════════════════════════════════════════════════════════════

class PaymentStore:
    """Tracks payments against invoices."""

    def add_payment(self, invoice_id: str, amount: float, currency: str,
                    payment_date: str = None, method: str = '',
                    notes: str = '') -> Dict:
        pay_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO payments (id, account_name, amount, currency, direction,
               description, payment_date, reference, invoice_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (pay_id, method or 'bank', amount, currency, 'out', notes,
             payment_date or datetime.utcnow().strftime('%Y-%m-%d'),
             '', invoice_id or None, datetime.utcnow()),
        )
        row = db.one("SELECT * FROM payments WHERE id = %s", (pay_id,))
        return self._row(row) if row else {}

    def get_for_invoice(self, invoice_id: str) -> List[Dict]:
        rows = db.query(
            "SELECT * FROM payments WHERE invoice_id = %s ORDER BY payment_date DESC",
            (invoice_id,),
        )
        return [self._row(r) for r in rows]

    def get_all(self) -> List[Dict]:
        rows = db.query("SELECT * FROM payments ORDER BY payment_date DESC")
        return [self._row(r) for r in rows]

    def total_payments_for_period(self, date_from: str = None,
                                  date_to: str = None) -> float:
        sql = "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE TRUE"
        params: list = []
        if date_from:
            sql += " AND payment_date >= %s"; params.append(date_from)
        if date_to:
            sql += " AND payment_date <= %s"; params.append(date_to)
        row = db.one(sql, tuple(params))
        return float((row or {}).get('total', 0))

    @staticmethod
    def _row(r: dict) -> dict:
        if not r:
            return {}
        def _dt(v):
            if v is None: return ''
            if hasattr(v, 'isoformat'): return v.isoformat()
            return str(v)
        return {
            'id':            r.get('id', ''),
            'invoice_id':    r.get('invoice_id', ''),
            'account_name':  r.get('account_name', ''),
            'amount':        float(r.get('amount', 0)),
            'currency':      r.get('currency', 'TND'),
            'direction':     r.get('direction', 'out'),
            'description':   r.get('description', ''),
            'payment_date':  _dt(r.get('payment_date')),
            'reference':     r.get('reference', ''),
            'method':        r.get('account_name', ''),
            'notes':         r.get('description', ''),
            'created_at':    _dt(r.get('created_at')),
        }


# ═══════════════════════════════════════════════════════════════════
# Exchange Rate Manager
# ═══════════════════════════════════════════════════════════════════

class ExchangeRates:
    """Simple currency conversion with user-updatable rates (kv_settings backed)."""

    def __init__(self):
        row = db.one("SELECT value FROM kv_settings WHERE key = 'exchange_rates'")
        if row and isinstance(row.get('value'), dict):
            self.settings = row['value']
        else:
            self.settings = {'eur_to_tnd': 3.35, 'tnd_to_eur': 0.2985}
            self._save()

    def _save(self):
        db.execute(
            """INSERT INTO kv_settings (key, value, updated_at)
               VALUES ('exchange_rates', %s::jsonb, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
            (json.dumps(self.settings), datetime.utcnow()),
        )

    def get_rate(self, from_cur: str, to_cur: str) -> float:
        if from_cur == to_cur:
            return 1.0
        return self.settings.get(f"{from_cur.lower()}_to_{to_cur.lower()}", 1.0)

    def set_rate(self, from_cur: str, to_cur: str, rate: float):
        self.settings[f"{from_cur.lower()}_to_{to_cur.lower()}"] = rate
        if rate > 0:
            self.settings[f"{to_cur.lower()}_to_{from_cur.lower()}"] = round(1.0 / rate, 6)
        self._save()

    def convert(self, amount: float, from_cur: str, to_cur: str) -> float:
        return amount * self.get_rate(from_cur, to_cur)

    def to_tnd(self, amount: float, currency: str) -> float:
        return self.convert(amount, currency, 'TND')

    def get_all_rates(self) -> dict:
        return dict(self.settings)
