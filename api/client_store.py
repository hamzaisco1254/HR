"""
Client store — Postgres-backed.

Clients are external entities we bill (e.g. the German master-contract
customer, plus other commercial clients). Schema is created by
db_schema.py — this module only handles CRUD.
"""
import uuid
from datetime import datetime
from typing import Optional

import db


VALID_COUNTRIES_HINT = (
    'TN', 'DE', 'FR', 'IT', 'ES', 'BE', 'NL', 'LU', 'CH', 'GB', 'US',
)
VALID_CURRENCIES = ('TND', 'EUR', 'USD', 'GBP', 'CHF')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _normalize_country(value: str) -> str:
    code = (value or '').strip().upper()
    return code or 'TN'


def _normalize_currency(value: str) -> str:
    code = (value or '').strip().upper()
    return code if code in VALID_CURRENCIES else 'EUR'


def _row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {
        'id':               r['id'],
        'name':             r['name'],
        'legal_name':       r.get('legal_name') or '',
        'country':          r.get('country') or 'TN',
        'default_currency': r.get('default_currency') or 'EUR',
        'vat_id':           r.get('vat_id') or '',
        'email':            r.get('email') or '',
        'phone':            r.get('phone') or '',
        'address':          r.get('address') or '',
        'notes':            r.get('notes') or '',
        'active':           bool(r.get('active', True)),
        'created_at':       str(r.get('created_at') or ''),
        'updated_at':       str(r.get('updated_at') or ''),
    }


class ClientStore:
    def __init__(self, **_):
        pass  # schema bootstrapped by db_schema.py

    def list_clients(self, include_inactive: bool = False) -> list:
        if include_inactive:
            rows = db.query("SELECT * FROM clients ORDER BY active DESC, name ASC")
        else:
            rows = db.query("SELECT * FROM clients WHERE active = TRUE ORDER BY name ASC")
        return [_row(r) for r in rows]

    def get(self, cid: str) -> Optional[dict]:
        if not cid:
            return None
        return _row(db.one("SELECT * FROM clients WHERE id = %s", (cid,)))

    def add(self, data: dict, created_by: Optional[str] = None) -> dict:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError("Nom du client requis.")
        # Reject duplicate name (case-insensitive)
        existing = db.one(
            "SELECT id FROM clients WHERE LOWER(name) = LOWER(%s)", (name,)
        )
        if existing:
            raise ValueError(f"Un client portant le nom '{name}' existe deja.")

        cid = _new_id()
        db.execute(
            """INSERT INTO clients
                 (id, name, legal_name, country, default_currency, vat_id,
                  email, phone, address, notes, active, created_by,
                  created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (cid, name,
             (data.get('legal_name') or '').strip() or None,
             _normalize_country(data.get('country', '')),
             _normalize_currency(data.get('default_currency', '')),
             (data.get('vat_id') or '').strip() or None,
             (data.get('email') or '').strip() or None,
             (data.get('phone') or '').strip() or None,
             (data.get('address') or '').strip() or None,
             (data.get('notes') or '').strip() or None,
             bool(data.get('active', True)),
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(cid) or {'id': cid}

    def update(self, cid: str, patch: dict) -> Optional[dict]:
        if not cid or not self.get(cid):
            return None

        sets, params = [], []
        text_fields = ('name', 'legal_name', 'vat_id', 'email', 'phone', 'address', 'notes')
        for f in text_fields:
            if f in patch:
                v = (patch[f] or '').strip()
                if f == 'name' and not v:
                    raise ValueError("Nom du client requis.")
                if f == 'name':
                    # check duplicate (excluding self)
                    dup = db.one(
                        "SELECT id FROM clients WHERE LOWER(name) = LOWER(%s) AND id <> %s",
                        (v, cid),
                    )
                    if dup:
                        raise ValueError(f"Un autre client porte deja le nom '{v}'.")
                sets.append(f"{f} = %s")
                params.append(v if v else None)

        if 'country' in patch:
            sets.append("country = %s")
            params.append(_normalize_country(patch['country']))
        if 'default_currency' in patch:
            sets.append("default_currency = %s")
            params.append(_normalize_currency(patch['default_currency']))
        if 'active' in patch:
            sets.append("active = %s")
            params.append(bool(patch['active']))

        if not sets:
            return self.get(cid)

        sets.append("updated_at = %s")
        params.append(datetime.utcnow())
        params.append(cid)
        db.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(cid)

    def deactivate(self, cid: str) -> bool:
        return db.execute(
            "UPDATE clients SET active = FALSE, updated_at = %s WHERE id = %s",
            (datetime.utcnow(), cid),
        ) > 0

    def delete(self, cid: str) -> bool:
        # Hard delete blocked by RESTRICT FK from projects — caller must
        # check there are no dependent projects, or call deactivate() instead.
        return db.execute("DELETE FROM clients WHERE id = %s", (cid,)) > 0

    def has_projects(self, cid: str) -> bool:
        row = db.one("SELECT 1 FROM projects WHERE client_id = %s LIMIT 1", (cid,))
        return row is not None
