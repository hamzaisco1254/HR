"""
Financial data persistence layer.

Handles storage for invoices, financial balances, payments, and audit trails.
Uses /tmp JSON files (survives warm Vercel invocations) with optional KV
persistence (set KV_REST_API_URL + KV_REST_API_TOKEN env vars).

Schema
------
Invoice:
    id, supplier_name, invoice_date, due_date, amount, currency,
    payment_status, invoice_ref, extracted_fields, confidence_scores,
    original_filename, notes, created_at, updated_at

FinancialAccount:
    id, name, type (bank|cash), currency, balance, updated_at

Payment:
    id, invoice_id, amount, currency, payment_date, method, notes

AuditEntry:
    id, entity_type, entity_id, action, changes, timestamp, user
"""
import json
import os
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, date
from typing import Any, Dict, List, Optional

_TMP_DIR = tempfile.gettempdir()
_INVOICES_PATH = os.path.join(_TMP_DIR, 'hr_invoices.json')
_BALANCES_PATH = os.path.join(_TMP_DIR, 'hr_balances.json')
_PAYMENTS_PATH = os.path.join(_TMP_DIR, 'hr_payments.json')
_AUDIT_PATH = os.path.join(_TMP_DIR, 'hr_audit.json')
_SETTINGS_PATH = os.path.join(_TMP_DIR, 'hr_fin_settings.json')


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------

class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


def _dumps(obj) -> str:
    return json.dumps(obj, cls=_Encoder, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# KV helpers (shared pattern from existing codebase)
# ---------------------------------------------------------------------------

def _kv_get(key: str):
    url = os.environ.get('KV_REST_API_URL')
    token = os.environ.get('KV_REST_API_TOKEN')
    if not url or not token:
        return None
    try:
        import requests as _req
        resp = _req.get(f"{url.rstrip('/')}/get/{key}",
                        headers={'Authorization': f'Bearer {token}'}, timeout=3)
        if resp.ok:
            raw = resp.json().get('result')
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return None


def _kv_set(key: str, value):
    url = os.environ.get('KV_REST_API_URL')
    token = os.environ.get('KV_REST_API_TOKEN')
    if not url or not token:
        return
    try:
        import requests as _req
        _req.post(f"{url.rstrip('/')}/set/{key}",
                  headers={'Authorization': f'Bearer {token}'},
                  json={'value': _dumps(value)}, timeout=3)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Generic JSON-file store
# ---------------------------------------------------------------------------

def _load(path: str, kv_key: str) -> list:
    data = _kv_get(kv_key)
    if data is not None:
        return data
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save(path: str, kv_key: str, data: list):
    _kv_set(kv_key, data)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_dumps(data))
    except IOError:
        pass


# ---------------------------------------------------------------------------
# Settings store (exchange rates, etc.)
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    data = _kv_get('hr_fin_settings')
    if data:
        return data
    if os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'eur_to_tnd': 3.35, 'tnd_to_eur': 0.2985}


def _save_settings(data: dict):
    _kv_set('hr_fin_settings', data)
    try:
        with open(_SETTINGS_PATH, 'w', encoding='utf-8') as f:
            f.write(_dumps(data))
    except IOError:
        pass


# ═══════════════════════════════════════════════════════════════════
# Invoice Store
# ═══════════════════════════════════════════════════════════════════

class InvoiceStore:
    """CRUD for invoices with audit trail."""

    def __init__(self):
        self.invoices: List[Dict] = _load(_INVOICES_PATH, 'hr_invoices')
        self.audit: List[Dict] = _load(_AUDIT_PATH, 'hr_audit')

    def _persist(self):
        _save(_INVOICES_PATH, 'hr_invoices', self.invoices)
        _save(_AUDIT_PATH, 'hr_audit', self.audit)

    def _audit(self, entity_id: str, action: str, changes: Any = None):
        self.audit.append({
            'id': str(uuid.uuid4()),
            'entity_type': 'invoice',
            'entity_id': entity_id,
            'action': action,
            'changes': changes,
            'timestamp': datetime.now().isoformat(),
        })

    # ── Create ──────────────────────────────────────────────────
    def add_invoice(self, data: Dict) -> Dict:
        now = datetime.now().isoformat()
        invoice = {
            'id': str(uuid.uuid4()),
            'supplier_name': data.get('supplier_name', ''),
            'invoice_date': data.get('invoice_date', now[:10]),
            'due_date': data.get('due_date', ''),
            'amount': float(data.get('amount', 0)),
            'currency': data.get('currency', 'TND'),
            'payment_status': data.get('payment_status', 'unpaid'),
            'invoice_ref': data.get('invoice_ref', ''),
            'extracted_fields': data.get('extracted_fields', {}),
            'confidence_scores': data.get('confidence_scores', {}),
            'original_filename': data.get('original_filename', ''),
            'notes': data.get('notes', ''),
            'created_at': now,
            'updated_at': now,
        }
        self.invoices.append(invoice)
        self._audit(invoice['id'], 'created', invoice)
        self._persist()
        return invoice

    # ── Read ────────────────────────────────────────────────────
    def get_all(self, filters: Optional[Dict] = None) -> List[Dict]:
        result = list(self.invoices)
        if not filters:
            return sorted(result, key=lambda x: x.get('created_at', ''), reverse=True)

        if filters.get('status'):
            result = [i for i in result if i['payment_status'] == filters['status']]
        if filters.get('currency'):
            result = [i for i in result if i['currency'] == filters['currency']]
        if filters.get('supplier'):
            s = filters['supplier'].lower()
            result = [i for i in result if s in i.get('supplier_name', '').lower()]
        if filters.get('date_from'):
            result = [i for i in result if i.get('invoice_date', '') >= filters['date_from']]
        if filters.get('date_to'):
            result = [i for i in result if i.get('invoice_date', '') <= filters['date_to']]

        return sorted(result, key=lambda x: x.get('created_at', ''), reverse=True)

    def get_by_id(self, invoice_id: str) -> Optional[Dict]:
        for inv in self.invoices:
            if inv['id'] == invoice_id:
                return inv
        return None

    # ── Update ──────────────────────────────────────────────────
    def update_invoice(self, invoice_id: str, updates: Dict) -> Optional[Dict]:
        for i, inv in enumerate(self.invoices):
            if inv['id'] == invoice_id:
                old = deepcopy(inv)
                for k, v in updates.items():
                    if k in ('id', 'created_at'):
                        continue
                    inv[k] = v
                inv['updated_at'] = datetime.now().isoformat()
                self.invoices[i] = inv
                self._audit(invoice_id, 'updated', {
                    'before': {k: old.get(k) for k in updates},
                    'after': {k: inv.get(k) for k in updates},
                })
                self._persist()
                return inv
        return None

    # ── Delete ──────────────────────────────────────────────────
    def delete_invoice(self, invoice_id: str) -> bool:
        for i, inv in enumerate(self.invoices):
            if inv['id'] == invoice_id:
                self._audit(invoice_id, 'deleted', inv)
                del self.invoices[i]
                self._persist()
                return True
        return False

    # ── Aggregations ────────────────────────────────────────────
    def total_by_status(self, currency: str = None) -> Dict:
        paid = unpaid = overdue = 0.0
        for inv in self.invoices:
            if currency and inv['currency'] != currency:
                continue
            amt = float(inv.get('amount', 0))
            s = inv.get('payment_status', 'unpaid')
            if s == 'paid':
                paid += amt
            elif s == 'overdue':
                overdue += amt
            else:
                unpaid += amt
        return {'paid': paid, 'unpaid': unpaid, 'overdue': overdue}

    def total_by_supplier(self) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for inv in self.invoices:
            supplier = inv.get('supplier_name', 'Inconnu')
            totals[supplier] = totals.get(supplier, 0) + float(inv.get('amount', 0))
        return dict(sorted(totals.items(), key=lambda x: x[1], reverse=True))

    def get_audit_trail(self, entity_id: str = None) -> List[Dict]:
        if entity_id:
            return [a for a in self.audit if a.get('entity_id') == entity_id]
        return sorted(self.audit, key=lambda x: x.get('timestamp', ''), reverse=True)


# ═══════════════════════════════════════════════════════════════════
# Financial Balances Store
# ═══════════════════════════════════════════════════════════════════

class BalanceStore:
    """CRUD for bank accounts and cash balances."""

    def __init__(self):
        self.accounts: List[Dict] = _load(_BALANCES_PATH, 'hr_balances')
        if not self.accounts:
            # Initialize defaults matching PLW Tunisia's likely accounts
            self.accounts = [
                {'id': str(uuid.uuid4()), 'name': 'Compte Bancaire TND',
                 'type': 'bank', 'currency': 'TND', 'balance': 0.0,
                 'updated_at': datetime.now().isoformat()},
                {'id': str(uuid.uuid4()), 'name': 'Compte Bancaire EUR',
                 'type': 'bank', 'currency': 'EUR', 'balance': 0.0,
                 'updated_at': datetime.now().isoformat()},
                {'id': str(uuid.uuid4()), 'name': 'Caisse (Espèces)',
                 'type': 'cash', 'currency': 'TND', 'balance': 0.0,
                 'updated_at': datetime.now().isoformat()},
            ]
            self._persist()

    def _persist(self):
        _save(_BALANCES_PATH, 'hr_balances', self.accounts)

    def get_all(self) -> List[Dict]:
        return list(self.accounts)

    def update_balance(self, account_id: str, balance: float) -> Optional[Dict]:
        for i, acc in enumerate(self.accounts):
            if acc['id'] == account_id:
                acc['balance'] = balance
                acc['updated_at'] = datetime.now().isoformat()
                self.accounts[i] = acc
                self._persist()
                return acc
        return None

    def add_account(self, name: str, acc_type: str, currency: str,
                    balance: float = 0.0) -> Dict:
        acc = {
            'id': str(uuid.uuid4()),
            'name': name,
            'type': acc_type,
            'currency': currency,
            'balance': balance,
            'updated_at': datetime.now().isoformat(),
        }
        self.accounts.append(acc)
        self._persist()
        return acc

    def delete_account(self, account_id: str) -> bool:
        for i, acc in enumerate(self.accounts):
            if acc['id'] == account_id:
                del self.accounts[i]
                self._persist()
                return True
        return False

    def total_by_currency(self) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for acc in self.accounts:
            cur = acc['currency']
            totals[cur] = totals.get(cur, 0) + float(acc.get('balance', 0))
        return totals


# ═══════════════════════════════════════════════════════════════════
# Payment Store
# ═══════════════════════════════════════════════════════════════════

class PaymentStore:
    """Tracks payments against invoices."""

    def __init__(self):
        self.payments: List[Dict] = _load(_PAYMENTS_PATH, 'hr_payments')

    def _persist(self):
        _save(_PAYMENTS_PATH, 'hr_payments', self.payments)

    def add_payment(self, invoice_id: str, amount: float, currency: str,
                    payment_date: str = None, method: str = '',
                    notes: str = '') -> Dict:
        payment = {
            'id': str(uuid.uuid4()),
            'invoice_id': invoice_id,
            'amount': amount,
            'currency': currency,
            'payment_date': payment_date or datetime.now().strftime('%Y-%m-%d'),
            'method': method,
            'notes': notes,
            'created_at': datetime.now().isoformat(),
        }
        self.payments.append(payment)
        self._persist()
        return payment

    def get_for_invoice(self, invoice_id: str) -> List[Dict]:
        return [p for p in self.payments if p['invoice_id'] == invoice_id]

    def get_all(self) -> List[Dict]:
        return sorted(self.payments, key=lambda x: x.get('payment_date', ''),
                       reverse=True)

    def total_payments_for_period(self, date_from: str = None,
                                  date_to: str = None) -> float:
        total = 0.0
        for p in self.payments:
            pd = p.get('payment_date', '')
            if date_from and pd < date_from:
                continue
            if date_to and pd > date_to:
                continue
            total += float(p.get('amount', 0))
        return total


# ═══════════════════════════════════════════════════════════════════
# Exchange Rate Manager
# ═══════════════════════════════════════════════════════════════════

class ExchangeRates:
    """Simple currency conversion with user-updatable rates."""

    def __init__(self):
        self.settings = _load_settings()

    def get_rate(self, from_cur: str, to_cur: str) -> float:
        if from_cur == to_cur:
            return 1.0
        key = f"{from_cur.lower()}_to_{to_cur.lower()}"
        return self.settings.get(key, 1.0)

    def set_rate(self, from_cur: str, to_cur: str, rate: float):
        self.settings[f"{from_cur.lower()}_to_{to_cur.lower()}"] = rate
        # Auto-compute inverse
        if rate > 0:
            self.settings[f"{to_cur.lower()}_to_{from_cur.lower()}"] = round(1.0 / rate, 6)
        _save_settings(self.settings)

    def convert(self, amount: float, from_cur: str, to_cur: str) -> float:
        return amount * self.get_rate(from_cur, to_cur)

    def to_tnd(self, amount: float, currency: str) -> float:
        return self.convert(amount, currency, 'TND')

    def get_all_rates(self) -> dict:
        return dict(self.settings)
