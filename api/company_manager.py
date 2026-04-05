"""Company data persistence manager (web/Vercel version).

Reads initial data from config/company.json (bundled with repo).
User edits are saved to /tmp/hr_company.json so they survive warm
invocations.  KV persistence is used when KV_REST_API_URL is set.
"""
import json
import os
import tempfile

_TMP_PATH = os.path.join(tempfile.gettempdir(), 'hr_company.json')
_API_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_API_DIR)
_PROJECT_PATH = os.path.join(_ROOT_DIR, 'config', 'company.json')

_DEFAULTS = {
    "name": "PLW Tunisia",
    "legal_id": "1692096F/A/M/000",
    "representative_name": "Lobna MILED",
    "address": "sise au 56, Boulevard de la Corniche Avenue Béji Caïd Essebsi Les Jardins du Lac, Lac2-Tunis",
    "city": "Tunis",
}


class CompanyManager:
    """Manages persistent company data storage."""

    def __init__(self):
        self.data = self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        # 1. Try KV
        kv = _kv_get('hr_company')
        if kv:
            return kv

        # 2. Try /tmp (user-modified)
        if os.path.exists(_TMP_PATH):
            try:
                with open(_TMP_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass

        # 3. Fall back to bundled config
        if os.path.exists(_PROJECT_PATH):
            try:
                with open(_PROJECT_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass

        return dict(_DEFAULTS)

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def _save(self):
        _kv_set('hr_company', self.data)
        try:
            with open(_TMP_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Warning: could not write company data: {e}")

    # ------------------------------------------------------------------
    # Public API  (matches desktop CompanyManager interface)
    # ------------------------------------------------------------------

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
        self._save()

    def set_field(self, field: str, value: str):
        self.data[field] = value
        self._save()


# ---------------------------------------------------------------------------
# KV helpers (shared pattern)
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
