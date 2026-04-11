"""Append-only audit log for user-initiated actions.

Persisted to Vercel KV when available, otherwise to /tmp (ephemeral).
Each entry captures the actor, the action, the target entity, the
client IP, and a short JSON payload of relevant details.

Usage:
    from audit_store import audit_store, audit

    # Manual logging
    audit_store.record('user.login', actor_id='...', actor_email='...',
                       entity_type='user', entity_id='...',
                       ip='1.2.3.4', ok=True)

    # Decorator usage (captures session user automatically)
    @audit(action='invoice.delete', entity='invoice')
    def api_invoice_delete():
        ...

The KV list is capped at MAX_ENTRIES so it never grows unbounded.
"""
import json
import os
import tempfile
import uuid
from datetime import datetime
from functools import wraps
from typing import Any, Dict, List, Optional

from flask import request, session, jsonify


_KV_KEY       = 'hr_audit_log'
_TMP_PATH     = os.path.join(tempfile.gettempdir(), 'hr_audit_log.json')
MAX_ENTRIES   = 2000   # Keep recent 2k events; older ones rotate out
PREVIEW_CHARS = 300    # Cap the details blob size per entry


# ── KV helpers (same pattern used across the codebase) ──────────────

def _kv_get() -> Optional[list]:
    url   = (os.environ.get('KV_REST_API_URL') or '').rstrip('/')
    token = os.environ.get('KV_REST_API_TOKEN') or ''
    if not url or not token:
        return None
    try:
        import requests as _req
        r = _req.get(
            f"{url}/get/{_KV_KEY}",
            headers={'Authorization': f'Bearer {token}'},
            timeout=2,
        )
        if r.ok:
            raw = r.json().get('result')
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return None


def _kv_set(entries: list):
    url   = (os.environ.get('KV_REST_API_URL') or '').rstrip('/')
    token = os.environ.get('KV_REST_API_TOKEN') or ''
    if not url or not token:
        return
    try:
        import requests as _req
        _req.post(
            f"{url}/set/{_KV_KEY}",
            headers={'Authorization': f'Bearer {token}'},
            json={'value': json.dumps(entries, ensure_ascii=False)},
            timeout=3,
        )
    except Exception:
        pass


# ── Audit store ─────────────────────────────────────────────────────

class AuditStore:
    """In-memory cache of audit entries with KV + /tmp persistence."""

    def __init__(self):
        self.entries: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        kv = _kv_get()
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
        # Cap size before saving
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]
        _kv_set(self.entries)
        try:
            with open(_TMP_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.entries, f, ensure_ascii=False)
        except IOError:
            pass

    # ── Record a single event ─────────────────────────────────────
    def record(
        self,
        action: str,
        actor_id: str = '',
        actor_email: str = '',
        entity_type: str = '',
        entity_id: str = '',
        ip: str = '',
        ok: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ):
        payload = ''
        if details:
            try:
                payload = json.dumps(details, ensure_ascii=False)[:PREVIEW_CHARS]
            except Exception:
                payload = str(details)[:PREVIEW_CHARS]

        entry = {
            'id':           uuid.uuid4().hex[:12],
            'ts':           datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            'action':       action,
            'actor_id':     actor_id,
            'actor_email':  actor_email,
            'entity_type':  entity_type,
            'entity_id':    entity_id,
            'ip':           ip,
            'ok':           bool(ok),
            'details':      payload,
        }
        self.entries.append(entry)
        self._save()

    # ── Queries ────────────────────────────────────────────────────
    def list_recent(self, limit: int = 100, action_filter: str = '') -> List[Dict[str, Any]]:
        items = self.entries
        if action_filter:
            items = [e for e in items if action_filter in e.get('action', '')]
        return list(reversed(items[-limit:]))

    def stats(self) -> Dict[str, Any]:
        total = len(self.entries)
        by_action: Dict[str, int] = {}
        for e in self.entries[-500:]:
            a = e.get('action', 'unknown')
            by_action[a] = by_action.get(a, 0) + 1
        return {
            'total_entries':   total,
            'recent_by_action': by_action,
        }


# Module-level singleton
audit_store = AuditStore()


# ── Helpers to extract request context ──────────────────────────────

def _current_ip() -> str:
    xff = request.headers.get('X-Forwarded-For', '') if request else ''
    if xff:
        return xff.split(',')[0].strip()
    try:
        return request.remote_addr or 'unknown'
    except Exception:
        return 'unknown'


def _current_actor() -> Dict[str, str]:
    try:
        return {
            'actor_id':    session.get('user_id', '') or '',
            'actor_email': session.get('user_email', '') or '',
        }
    except Exception:
        return {'actor_id': '', 'actor_email': ''}


# ── Decorator for Flask routes ──────────────────────────────────────

def audit(action: str, entity: str = '', entity_arg: str = ''):
    """Flask route decorator that records an audit entry.

    Args:
        action: dotted action name, e.g. "invoice.delete"
        entity: entity type label, e.g. "invoice"
        entity_arg: request arg name to extract entity id from
                    (checks POST json / form / query string / route kwarg)
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            entity_id = ''
            if entity_arg:
                # Try route kwargs first, then JSON body, then form, then query
                if entity_arg in kwargs:
                    entity_id = str(kwargs.get(entity_arg) or '')
                else:
                    try:
                        body = request.get_json(silent=True) or {}
                        if isinstance(body, dict) and entity_arg in body:
                            entity_id = str(body[entity_arg])
                    except Exception:
                        pass
                    if not entity_id:
                        entity_id = request.form.get(entity_arg, '') or request.args.get(entity_arg, '')

            success = True
            try:
                response = fn(*args, **kwargs)
                # Flask returns (body, status) tuples sometimes
                if isinstance(response, tuple) and len(response) >= 2:
                    status = response[1]
                    if isinstance(status, int) and status >= 400:
                        success = False
                return response
            except Exception:
                success = False
                raise
            finally:
                try:
                    actor = _current_actor()
                    audit_store.record(
                        action=action,
                        actor_id=actor['actor_id'],
                        actor_email=actor['actor_email'],
                        entity_type=entity,
                        entity_id=entity_id,
                        ip=_current_ip(),
                        ok=success,
                    )
                except Exception:
                    # Audit failures must never break the request
                    pass
        return wrapper
    return decorator
