"""Append-only audit log — Postgres-backed.

Each entry captures the actor, the action, the target entity, the
client IP, and a short JSON payload of relevant details.

Usage:
    from audit_store import audit_store, audit

    audit_store.record('user.login', actor_id='...', ...)

    @audit(action='invoice.delete', entity='invoice')
    def api_invoice_delete():
        ...
"""
import json
import uuid
from datetime import datetime
from functools import wraps
from typing import Any, Dict, List, Optional

from flask import request, session, jsonify

import db


# ═══════════════════════════════════════════════════════════════════
# Audit store (Postgres)
# ═══════════════════════════════════════════════════════════════════

class AuditStore:
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
        detail_json = None
        if details:
            try:
                detail_json = json.dumps(details, ensure_ascii=False, default=str)[:500]
            except Exception:
                detail_json = str(details)[:500]

        try:
            db.execute(
                """INSERT INTO audit_log (id, ts, action, actor_id, actor_email,
                   entity_type, entity_id, ip, ok, details)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
                (
                    uuid.uuid4().hex[:12],
                    datetime.utcnow(),
                    action,
                    actor_id or None,
                    actor_email or None,
                    entity_type or None,
                    entity_id or None,
                    ip or None,
                    ok,
                    detail_json,
                ),
            )
        except Exception:
            # Audit failures must never break the request
            pass

    def list_recent(self, limit: int = 100, action_filter: str = '') -> List[Dict[str, Any]]:
        if action_filter:
            rows = db.query(
                "SELECT * FROM audit_log WHERE action ILIKE %s ORDER BY ts DESC LIMIT %s",
                (f'%{action_filter}%', limit),
            )
        else:
            rows = db.query(
                "SELECT * FROM audit_log ORDER BY ts DESC LIMIT %s",
                (limit,),
            )
        # Convert ts to ISO string for JSON serialisation
        for r in rows:
            if r.get('ts'):
                r['ts'] = r['ts'].isoformat()
            if r.get('details') and isinstance(r['details'], dict):
                r['details'] = json.dumps(r['details'], ensure_ascii=False)
        return rows

    def stats(self) -> Dict[str, Any]:
        total = (db.one("SELECT COUNT(*) AS cnt FROM audit_log") or {}).get('cnt', 0)
        action_rows = db.query(
            "SELECT action, COUNT(*) AS cnt FROM audit_log GROUP BY action ORDER BY cnt DESC LIMIT 15"
        )
        by_action = {r['action']: r['cnt'] for r in action_rows}
        return {'total_entries': total, 'recent_by_action': by_action}


# Module-level singleton
audit_store = AuditStore()


# ── Helpers ──────────────────────────────────────────────────────────

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
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            entity_id = ''
            if entity_arg:
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
                    pass
        return wrapper
    return decorator
