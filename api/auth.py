"""
Authentication & access management module — Postgres-backed.

Features:
- Admin account auto-seeded via db_schema.py bootstrap
- User accounts stored in `users` table
- Password hashing via werkzeug
- Flask session-based auth (signed cookies)

REQUIRED environment variables:
- ADMIN_EMAIL / ADMIN_PASSWORD: read by db_schema.bootstrap()
"""
import os
import secrets as _secrets
import uuid
from datetime import datetime
from functools import wraps
from typing import Dict, List, Optional

from flask import session, redirect, url_for, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

import db

MAX_USERS = int(os.environ.get('MAX_USERS', '100'))

# ── Password policy ─────────────────────────────────────────────────
MIN_PASSWORD_LENGTH = 12


def validate_password(password: str) -> None:
    """Raise ValueError if the password does not meet the policy."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
        )
    has_lower   = any(c.islower() for c in password)
    has_upper   = any(c.isupper() for c in password)
    has_digit   = any(c.isdigit() for c in password)
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


# ═══════════════════════════════════════════════════════════════════
# User Store (Postgres)
# ═══════════════════════════════════════════════════════════════════

_USER_FIELDS = 'id, email, name, role, active, created_at, last_login_at'

def _row_to_dict(row: dict) -> dict:
    """Convert a DB row to an API-friendly dict (no password_hash)."""
    if not row:
        return {}
    return {
        'id':            row['id'],
        'email':         row['email'],
        'name':          row.get('name', ''),
        'role':          row.get('role', 'user'),
        'active':        row.get('active', True),
        'created_at':    row.get('created_at', ''),
    }


class UserStore:
    """Manages user accounts in Postgres."""

    def __init__(self):
        # Schema bootstrap happens once at startup via db_schema
        pass

    # ── Authentication ──────────────────────────────────────────
    def authenticate(self, email: str, password: str) -> Optional[Dict]:
        row = db.one(
            "SELECT id, email, password_hash, name, role, active FROM users WHERE LOWER(email) = LOWER(%s)",
            (email.strip(),),
        )
        if not row:
            return None
        if not row.get('active', False):
            return None
        if check_password_hash(row['password_hash'], password):
            # Update last_login_at
            db.execute("UPDATE users SET last_login_at = %s WHERE id = %s",
                       (datetime.utcnow(), row['id']))
            return _row_to_dict(row)
        return None

    # ── User management (admin only) ────────────────────────────
    def get_all_users(self) -> List[Dict]:
        rows = db.query(f"SELECT {_USER_FIELDS} FROM users ORDER BY created_at")
        return [_row_to_dict(r) for r in rows]

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        row = db.one(f"SELECT {_USER_FIELDS} FROM users WHERE id = %s", (user_id,))
        return _row_to_dict(row) if row else None

    def add_user(self, email: str, password: str, name: str) -> Dict:
        current_count = (db.one("SELECT COUNT(*) AS cnt FROM users") or {}).get('cnt', 0)
        if current_count >= MAX_USERS:
            raise ValueError(f'Maximum {MAX_USERS} utilisateurs atteint.')

        email_lower = email.lower().strip()
        exists = db.one("SELECT id FROM users WHERE LOWER(email) = %s", (email_lower,))
        if exists:
            raise ValueError('Cet email est déjà utilisé.')
        if not email_lower or not password:
            raise ValueError('Email et mot de passe requis.')

        validate_password(password)

        user_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO users (id, email, password_hash, name, role, active, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user_id, email_lower, generate_password_hash(password),
             name or email_lower.split('@')[0], 'user', True, datetime.utcnow()),
        )
        return self.get_user_by_id(user_id) or {'id': user_id}

    def remove_user(self, user_id: str) -> bool:
        row = db.one("SELECT role FROM users WHERE id = %s", (user_id,))
        if not row:
            return False
        if row['role'] == 'admin':
            raise ValueError("Impossible de supprimer le compte administrateur.")
        return db.execute("DELETE FROM users WHERE id = %s", (user_id,)) > 0

    def toggle_user(self, user_id: str) -> Optional[Dict]:
        row = db.one("SELECT role, active FROM users WHERE id = %s", (user_id,))
        if not row:
            return None
        if row['role'] == 'admin':
            raise ValueError("Impossible de désactiver le compte administrateur.")
        new_active = not row['active']
        db.execute("UPDATE users SET active = %s WHERE id = %s", (new_active, user_id))
        return self.get_user_by_id(user_id)

    def change_password(self, user_id: str, new_password: str) -> bool:
        validate_password(new_password)
        return db.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (generate_password_hash(new_password), user_id),
        ) > 0

    def user_count(self) -> int:
        return (db.one("SELECT COUNT(*) AS cnt FROM users") or {}).get('cnt', 0)

    def active_count(self) -> int:
        return (db.one("SELECT COUNT(*) AS cnt FROM users WHERE active = TRUE") or {}).get('cnt', 0)


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
