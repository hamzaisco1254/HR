"""Simple sliding-window rate limiter for Flask routes on Vercel.

Persistence strategy:
    1. If KV_REST_API_URL is configured, use Vercel KV (survives cold starts)
    2. Otherwise, fall back to an in-process dict (per-instance only)

The limiter is key-scoped: each decorator uses a route tag + a subject
(authenticated user id, or client IP for anonymous endpoints). The window
is a strict sliding window backed by a short-lived list of timestamps.

Usage:
    from rate_limiter import rate_limit

    @app.route('/login', methods=['POST'])
    @rate_limit(tag='login', max_requests=5, window_seconds=60, by='ip')
    def login():
        ...

    @app.route('/api/chat/query', methods=['POST'])
    @login_required
    @rate_limit(tag='chat', max_requests=30, window_seconds=60, by='user')
    def chat_query():
        ...
"""
import json
import os
import time
from functools import wraps
from typing import Callable, List

from flask import request, session, jsonify


# ── KV helpers (same pattern used elsewhere) ─────────────────────────

def _kv_url() -> str:
    return (os.environ.get('KV_REST_API_URL') or '').rstrip('/')


def _kv_token() -> str:
    return os.environ.get('KV_REST_API_TOKEN') or ''


def _kv_get(key: str):
    if not _kv_url() or not _kv_token():
        return None
    try:
        import requests as _req
        r = _req.get(
            f"{_kv_url()}/get/{key}",
            headers={'Authorization': f'Bearer {_kv_token()}'},
            timeout=2,
        )
        if r.ok:
            raw = r.json().get('result')
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return None


def _kv_set(key: str, value, ttl_seconds: int = 120):
    if not _kv_url() or not _kv_token():
        return
    try:
        import requests as _req
        _req.post(
            f"{_kv_url()}/set/{key}",
            headers={'Authorization': f'Bearer {_kv_token()}'},
            json={'value': json.dumps(value), 'ex': ttl_seconds},
            timeout=2,
        )
    except Exception:
        pass


# ── In-process fallback store ────────────────────────────────────────
_memory: dict = {}


def _store_get(key: str) -> List[float]:
    kv = _kv_get(key)
    if kv is not None:
        return list(kv)
    return list(_memory.get(key, []))


def _store_set(key: str, timestamps: List[float], ttl_seconds: int):
    _memory[key] = timestamps
    _kv_set(key, timestamps, ttl_seconds=ttl_seconds)


# ── Subject resolution (user id or client IP) ────────────────────────

def _client_ip() -> str:
    # Vercel sets X-Forwarded-For with the real client IP first
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _subject_key(by: str) -> str:
    if by == 'user':
        uid = session.get('user_id')
        if uid:
            return f"user:{uid}"
    return f"ip:{_client_ip()}"


# ── Public decorator ─────────────────────────────────────────────────

def rate_limit(
    tag: str,
    max_requests: int,
    window_seconds: int,
    by: str = 'ip',  # 'ip' or 'user'
):
    """Decorate a Flask view with a sliding-window rate limit."""
    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            now     = time.time()
            subject = _subject_key(by)
            key     = f"rl:{tag}:{subject}"

            timestamps = _store_get(key)
            # keep only entries inside the window
            cutoff = now - window_seconds
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= max_requests:
                retry_after = int(window_seconds - (now - timestamps[0])) + 1
                resp = jsonify({
                    'error':       'Trop de requêtes. Réessayez dans quelques instants.',
                    'retry_after': retry_after,
                })
                resp.status_code = 429
                resp.headers['Retry-After'] = str(retry_after)
                return resp

            timestamps.append(now)
            _store_set(key, timestamps, ttl_seconds=window_seconds + 10)
            return fn(*args, **kwargs)

        return wrapper
    return decorator
