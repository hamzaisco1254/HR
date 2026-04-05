"""Reference management system — exact replica of the desktop ReferenceManager.

The desktop uses a SINGLE shared counter for both document types.
year_prefix and counter can be set independently (AttestationTab does this
before calling generate_reference()).

Storage: /tmp/hr_references.json (+ optional Vercel KV).
"""
import json
import os
import tempfile
from datetime import datetime
from typing import Optional, Dict

_TMP_PATH = os.path.join(tempfile.gettempdir(), 'hr_references.json')


class ReferenceManager:
    """Manages document reference generation and persistence."""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or _TMP_PATH
        self.data = self._load_data()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_data(self) -> Dict:
        kv = _kv_get('hr_references')
        if kv:
            return kv

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        return self._initialize_data()

    def _initialize_data(self) -> Dict:
        return {
            'year_prefix': datetime.now().year % 100,
            'counter': 0,
            'history': [],
        }

    def _save_data(self):
        _kv_set('hr_references', self.data)
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Warning: could not save references: {e}")

    # ------------------------------------------------------------------
    # Public API  — matches desktop ReferenceManager exactly
    # ------------------------------------------------------------------

    def generate_reference(self, year_prefix: Optional[int] = None) -> str:
        """Increment counter and return next reference string."""
        if year_prefix is not None:
            self.data['year_prefix'] = year_prefix

        self.data['counter'] += 1
        reference = f"{self.data['year_prefix']}/{self.data['counter']:04d}"

        self.data.setdefault('history', []).append({
            'reference': reference,
            'generated_at': datetime.now().isoformat(),
        })

        self._save_data()
        return reference

    def get_next_reference(self) -> str:
        """Preview the next reference without modifying state."""
        return f"{self.data['year_prefix']}/{self.data['counter'] + 1:04d}"

    def set_counter(self, value: int):
        self.data['counter'] = value
        self._save_data()

    def set_year_prefix(self, year: int):
        self.data['year_prefix'] = year
        self._save_data()

    def reset_counter(self):
        self.data['counter'] = 0
        self._save_data()

    def get_last_reference(self) -> Optional[str]:
        if self.data.get('history'):
            return self.data['history'][-1]['reference']
        return None

    def get_counter(self) -> int:
        return self.data.get('counter', 0)

    def get_year_prefix(self) -> int:
        return self.data.get('year_prefix', datetime.now().year % 100)

    # ------------------------------------------------------------------
    # Extra: formatted history for the references view (legacy compat)
    # ------------------------------------------------------------------

    def get_all_references(self) -> Dict:
        """Return data compatible with the references.html template."""
        history = self.data.get('history', [])
        from datetime import datetime as dt
        parsed = []
        for item in history:
            try:
                ts = dt.fromisoformat(item['generated_at'])
            except (KeyError, ValueError):
                ts = dt.now()
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


# ---------------------------------------------------------------------------
# KV helpers
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
    except Exception as e:
        print(f"KV get error: {e}")
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
                  json={'value': json.dumps(value, ensure_ascii=False)},
                  timeout=3)
    except Exception as e:
        print(f"KV set error: {e}")
