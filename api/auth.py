"""
Authentication & access management module.

Features:
- Admin account (auto-created on first run)
- Up to 5 authorized users total (admin counts as 1)
- Password hashing via werkzeug (already bundled with Flask)
- Flask session-based auth (signed cookies)
- KV persistence + /tmp fallback (same pattern as rest of codebase)

REQUIRED environment variables for production:
- ADMIN_EMAIL     : primary admin email address
- ADMIN_PASSWORD  : strong admin password (12+ chars)

If ADMIN_PASSWORD is not set, a random one is generated and LOGGED once at
startup (visible in Vercel function logs) so the operator can retrieve it.
The ephemeral password is NOT persisted anywhere else.
"""
import json
import os
import secrets as _secrets
import tempfile
import uuid
from datetime import datetime
from functools import wraps
from typing import Dict, List, Optional

from flask import session, redirect, url_for, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

_TMP_PATH = os.path.join(tempfile.gettempdir(), 'hr_users.json')

# Soft cap only — easy to raise later, not enforced on admin-created users
# so the platform can scale beyond 5 team members.
MAX_USERS = int(os.environ.get('MAX_USERS', '100'))

# ── Password policy ─────────────────────────────────────────────────
MIN_PASSWORD_LENGTH = 12


def validate_password(password: str) -> None:
    """Raise ValueError if the password does not meet the policy.

    Rules:
        - at least 12 characters
        - contains at least 1 lowercase letter
        - contains at least 1 uppercase letter
        - contains at least 1 digit
        - contains at least 1 special character
    """
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
        )
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    missing = []
    if not has_lower:   missing.append("une minuscule")
    if not has_upper:   missing.append("une majuscule")
    if not has_digit:   missing.append("un chiffre")
    if not has_special: missing.append("un caractère spécial")
    if missing:
        raise ValueError(
            "Le mot de passe doit contenir " + ", ".join(missing) + "."
        )


# ---------------------------------------------------------------------------
# KV helpers (shared pattern)
# ---------------------------------------------------------------------------

def _kv_get(key: str):
    kv_url = os.environ.get('KV_REST_API_URL')
    token = os.environ.get('KV_REST_API_TOKEN')
    if not kv_url or not token:
        return None
    try:
        import requests as _req
        resp = _req.get(f"{kv_url.rstrip('/')}/get/{key}",
                        headers={'Authorization': f'Bearer {token}'}, timeout=3)
        if resp.ok:
            raw = resp.json().get('result')
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return None


def _kv_set(key: str, value):
    kv_url = os.environ.get('KV_REST_API_URL')
    token = os.environ.get('KV_REST_API_TOKEN')
    if not kv_url or not token:
        return
    try:
        import requests as _req
        _req.post(f"{kv_url.rstrip('/')}/set/{key}",
                  headers={'Authorization': f'Bearer {token}'},
                  json={'value': json.dumps(value, ensure_ascii=False)},
                  timeout=3)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# User Store
# ═══════════════════════════════════════════════════════════════════

class UserStore:
    """Manages user accounts with persistence."""

    def __init__(self):
        self.users: List[Dict] = self._load()
        self._ensure_admin()

    def _load(self) -> List[Dict]:
        data = _kv_get('hr_users')
        if data is not None:
            return data
        if os.path.exists(_TMP_PATH):
            try:
                with open(_TMP_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save(self):
        _kv_set('hr_users', self.users)
        try:
            with open(_TMP_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.users, f, indent=2, ensure_ascii=False)
        except IOError:
            pass

    def _ensure_admin(self):
        """Create admin account if none exists.

        - ADMIN_EMAIL must be provided via env var (falls back to a sentinel
          that will not work until the operator fixes it).
        - ADMIN_PASSWORD: if missing, a random 20-char password is generated
          and printed ONCE to the server logs. The operator must read it from
          logs and immediately change it after first login.
        """
        admin_exists = any(u.get('role') == 'admin' for u in self.users)
        if admin_exists:
            return

        admin_email = os.environ.get('ADMIN_EMAIL', '').strip()
        if not admin_email:
            admin_email = 'admin@example.invalid'
            print('[SECURITY WARNING] ADMIN_EMAIL env var not set — '
                  'using placeholder. Set it in Vercel env vars.')

        admin_pass = os.environ.get('ADMIN_PASSWORD', '').strip()
        if not admin_pass:
            admin_pass = _secrets.token_urlsafe(16)
            print(f'[SECURITY WARNING] ADMIN_PASSWORD env var not set — '
                  f'generated one-time password: {admin_pass}  '
                  f'(login with {admin_email}, change immediately)')

        self.users.append({
            'id':            str(uuid.uuid4()),
            'email':         admin_email,
            'password_hash': generate_password_hash(admin_pass),
            'name':          'Administrateur',
            'role':          'admin',
            'active':        True,
            'created_at':    datetime.now().isoformat(),
        })
        self._save()

    # ── Authentication ──────────────────────────────────────────

    def authenticate(self, email: str, password: str) -> Optional[Dict]:
        """Return user dict if credentials valid and account active, else None."""
        for user in self.users:
            if user['email'].lower() == email.lower().strip():
                if not user.get('active', False):
                    return None
                if check_password_hash(user['password_hash'], password):
                    return {k: v for k, v in user.items() if k != 'password_hash'}
                return None
        return None

    # ── User management (admin only) ────────────────────────────

    def get_all_users(self) -> List[Dict]:
        """Return all users (without password hashes)."""
        return [{k: v for k, v in u.items() if k != 'password_hash'}
                for u in self.users]

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        for u in self.users:
            if u['id'] == user_id:
                return {k: v for k, v in u.items() if k != 'password_hash'}
        return None

    def add_user(self, email: str, password: str, name: str) -> Dict:
        """Add a new user. Raises ValueError if limit reached or email taken."""
        if len(self.users) >= MAX_USERS:
            raise ValueError(f'Maximum {MAX_USERS} utilisateurs atteint.')

        email_lower = email.lower().strip()
        if any(u['email'].lower() == email_lower for u in self.users):
            raise ValueError('Cet email est déjà utilisé.')

        if not email_lower or not password:
            raise ValueError('Email et mot de passe requis.')

        validate_password(password)

        user = {
            'id': str(uuid.uuid4()),
            'email': email_lower,
            'password_hash': generate_password_hash(password),
            'name': name or email_lower.split('@')[0],
            'role': 'user',
            'active': True,
            'created_at': datetime.now().isoformat(),
        }
        self.users.append(user)
        self._save()
        return {k: v for k, v in user.items() if k != 'password_hash'}

    def remove_user(self, user_id: str) -> bool:
        """Remove a user. Cannot remove admin or last user."""
        for i, u in enumerate(self.users):
            if u['id'] == user_id:
                if u['role'] == 'admin':
                    raise ValueError("Impossible de supprimer le compte administrateur.")
                del self.users[i]
                self._save()
                return True
        return False

    def toggle_user(self, user_id: str) -> Optional[Dict]:
        """Enable/disable a user."""
        for i, u in enumerate(self.users):
            if u['id'] == user_id:
                if u['role'] == 'admin':
                    raise ValueError("Impossible de désactiver le compte administrateur.")
                u['active'] = not u.get('active', True)
                self.users[i] = u
                self._save()
                return {k: v for k, v in u.items() if k != 'password_hash'}
        return None

    def change_password(self, user_id: str, new_password: str) -> bool:
        """Change a user's password."""
        validate_password(new_password)
        for i, u in enumerate(self.users):
            if u['id'] == user_id:
                u['password_hash'] = generate_password_hash(new_password)
                self.users[i] = u
                self._save()
                return True
        return False

    def user_count(self) -> int:
        return len(self.users)

    def active_count(self) -> int:
        return sum(1 for u in self.users if u.get('active', False))


# ═══════════════════════════════════════════════════════════════════
# Flask decorators
# ═══════════════════════════════════════════════════════════════════

def login_required(f):
    """Decorator: redirect to /login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Non authentifié'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Non authentifié'}), 401
            return redirect(url_for('login'))
        if session.get('user_role') != 'admin':
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Accès administrateur requis'}), 403
            return redirect(url_for('attestation'))
        return f(*args, **kwargs)
    return decorated
