"""Company data persistence manager — Postgres-backed.

Stores company configuration in the kv_settings table (key='company').
Falls back to COMPANY_JSON env var for initial seed, then to placeholder.
"""
import json
import os
from datetime import datetime

import db

_DEFAULTS = {
    "name":                "Your Company",
    "legal_id":            "",
    "representative_name": "",
    "address":             "",
    "city":                "",
}


class CompanyManager:
    """Manages persistent company data storage in Postgres."""

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        # 1. Postgres kv_settings table
        row = db.one("SELECT value FROM kv_settings WHERE key = 'company'")
        if row and row.get('value'):
            return {**_DEFAULTS, **(row['value'] if isinstance(row['value'], dict) else {})}

        # 2. COMPANY_JSON env var (production seed)
        env_blob = os.environ.get('COMPANY_JSON', '').strip()
        if env_blob:
            try:
                data = json.loads(env_blob)
                if isinstance(data, dict):
                    merged = {**_DEFAULTS, **data}
                    self._save_to_db(merged)
                    return merged
            except json.JSONDecodeError:
                pass

        return dict(_DEFAULTS)

    def _save_to_db(self, data: dict = None):
        data = data or self.data
        try:
            db.execute(
                """INSERT INTO kv_settings (key, value, updated_at)
                   VALUES ('company', %s::jsonb, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                (json.dumps(data, ensure_ascii=False), datetime.utcnow()),
            )
        except Exception:
            pass

    def get_company_data(self) -> dict:
        return self.data.copy()

    def get_field(self, field: str) -> str:
        return self.data.get(field, '')

    def set_company_data(self, name: str, legal_id: str, representative: str,
                         address: str, city: str):
        self.data = {
            'name': name,
            'legal_id': legal_id,
            'representative_name': representative,
            'address': address,
            'city': city,
        }
        self._save_to_db()

    def set_field(self, field: str, value: str):
        self.data[field] = value
        self._save_to_db()
