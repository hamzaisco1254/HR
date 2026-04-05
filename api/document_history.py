"""Document history manager (web/Vercel version).

History is stored in /tmp/hr_history.json and optionally in KV.
Matches the desktop DocumentHistory interface exactly.
"""
import json
import os
import tempfile
from datetime import datetime
from typing import List, Dict

_TMP_PATH = os.path.join(tempfile.gettempdir(), 'hr_history.json')


class DocumentHistory:
    """Manages the history of generated documents."""

    def __init__(self):
        self.history: List[Dict] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> List[Dict]:
        kv = _kv_get('hr_history')
        if kv is not None:
            return kv

        if os.path.exists(_TMP_PATH):
            try:
                with open(_TMP_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save(self):
        _kv_set('hr_history', self.history)
        try:
            with open(_TMP_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Warning: could not write history: {e}")

    # ------------------------------------------------------------------
    # Public API  (matches desktop DocumentHistory interface)
    # ------------------------------------------------------------------

    def add_document(self, reference: str, doc_type: str,
                     employee_name: str, file_path: str):
        entry = {
            'reference': reference,
            'doc_type': doc_type,
            'employee_name': employee_name,
            'file_path': str(file_path),
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        }
        self.history.append(entry)
        self._save()

    def get_all_documents(self) -> List[Dict]:
        return list(self.history)

    def get_documents_by_type(self, doc_type: str) -> List[Dict]:
        return [d for d in self.history if d.get('doc_type') == doc_type]

    def get_recent_documents(self, limit: int = 10) -> List[Dict]:
        return sorted(self.history,
                      key=lambda x: x.get('timestamp', ''),
                      reverse=True)[:limit]

    def clear_history(self):
        self.history = []
        self._save()


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
