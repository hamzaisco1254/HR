"""Reference management system — Postgres-backed.

The reference counter (year_prefix + counter) is stored in the
kv_settings table (key='references') so it survives cold starts
and redeployments.

Public API matches the legacy desktop ReferenceManager exactly.
"""
import json
from datetime import datetime
from typing import Optional, Dict

import db


_KV_KEY = 'references'


class ReferenceManager:
    """Manages document reference generation with Postgres persistence."""

    def __init__(self, **_):
        self.data = self._load_data()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_data(self) -> Dict:
        row = db.one("SELECT value FROM kv_settings WHERE key = %s", (_KV_KEY,))
        if row and isinstance(row.get('value'), dict):
            return row['value']
        # First-time init
        data = self._initialize_data()
        self._save_data(data)
        return data

    def _initialize_data(self) -> Dict:
        return {
            'year_prefix': datetime.now().year % 100,
            'counter': 0,
            'history': [],
        }

    def _save_data(self, data: Dict = None):
        data = data or self.data
        try:
            db.execute(
                """INSERT INTO kv_settings (key, value, updated_at)
                   VALUES (%s, %s::jsonb, %s)
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                (_KV_KEY, json.dumps(data, ensure_ascii=False, default=str),
                 datetime.utcnow()),
            )
        except Exception as e:
            print(f"Warning: could not save references: {e}")

    def _reload(self):
        """Re-read from DB to get latest state (important for concurrent requests)."""
        row = db.one("SELECT value FROM kv_settings WHERE key = %s", (_KV_KEY,))
        if row and isinstance(row.get('value'), dict):
            self.data = row['value']

    # ------------------------------------------------------------------
    # Public API  — matches desktop ReferenceManager exactly
    # ------------------------------------------------------------------

    def generate_reference(self, year_prefix: Optional[int] = None) -> str:
        """Increment counter and return next reference string."""
        # Re-read from DB to avoid stale state across serverless invocations
        self._reload()

        if year_prefix is not None:
            self.data['year_prefix'] = year_prefix

        self.data['counter'] += 1
        reference = f"{self.data['year_prefix']}/{self.data['counter']:04d}"

        self.data.setdefault('history', []).append({
            'reference': reference,
            'generated_at': datetime.now().isoformat(),
        })

        # Keep history manageable (last 200 entries)
        if len(self.data.get('history', [])) > 200:
            self.data['history'] = self.data['history'][-200:]

        self._save_data()
        return reference

    def get_next_reference(self) -> str:
        """Preview the next reference without modifying state."""
        self._reload()
        return f"{self.data['year_prefix']}/{self.data['counter'] + 1:04d}"

    def set_counter(self, value: int):
        self._reload()
        self.data['counter'] = value
        self._save_data()

    def set_year_prefix(self, year: int):
        self._reload()
        self.data['year_prefix'] = year
        self._save_data()

    def reset_counter(self):
        self._reload()
        self.data['counter'] = 0
        self._save_data()

    def get_last_reference(self) -> Optional[str]:
        self._reload()
        if self.data.get('history'):
            return self.data['history'][-1]['reference']
        return None

    def get_counter(self) -> int:
        self._reload()
        return self.data.get('counter', 0)

    def get_year_prefix(self) -> int:
        self._reload()
        return self.data.get('year_prefix', datetime.now().year % 100)

    # ------------------------------------------------------------------
    # Extra: formatted history for the references view (legacy compat)
    # ------------------------------------------------------------------

    def get_all_references(self) -> Dict:
        self._reload()
        history = self.data.get('history', [])
        parsed = []
        for item in history:
            try:
                ts = datetime.fromisoformat(item['generated_at'])
            except (KeyError, ValueError):
                ts = datetime.now()
            parsed.append({
                'type': item.get('type', 'attestation'),
                'reference': item.get('reference', ''),
                'timestamp': ts,
            })
        att = [h for h in parsed if h['type'] == 'attestation']
        mis = [h for h in parsed if h['type'] == 'ordre_mission']
        return {
            'attestation_counter': self.get_counter(),
            'attestation_current': att[-1]['reference'] if att else None,
            'mission_counter': self.get_counter(),
            'mission_current': mis[-1]['reference'] if mis else None,
            'history': parsed,
        }
