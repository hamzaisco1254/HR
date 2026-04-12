"""
Outlook AI Agent — scans emails for invoices using Microsoft Graph + Gemini.

Flow:
    1. User clicks "Connect Outlook" → OAuth2 redirect to Microsoft login
    2. Callback stores access_token + refresh_token in Postgres (kv_settings)
    3. "Scan Emails" fetches recent inbox messages via Microsoft Graph
    4. Two-stage detection:
       a) Keyword pre-filter (fast, no API cost)
       b) Gemini classification on pre-filtered emails (accurate)
    5. Detected invoices shown in UI → user clicks "Import"
    6. Attachment downloaded from Graph → processed by invoice_processor

Required env vars:
    OUTLOOK_CLIENT_ID     — Azure AD app registration client ID
    OUTLOOK_CLIENT_SECRET — Azure AD app registration client secret
    OUTLOOK_TENANT_ID     — 'common' (multi-tenant) or specific tenant ID
"""
import base64
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, quote

import requests as http

import db
from gemini_client import generate as gemini_generate, is_configured as gemini_ok

# ── Config ──────────────────────────────────────────────────────────
CLIENT_ID     = os.environ.get('OUTLOOK_CLIENT_ID', '').strip()
CLIENT_SECRET = os.environ.get('OUTLOOK_CLIENT_SECRET', '').strip()
TENANT_ID     = os.environ.get('OUTLOOK_TENANT_ID', 'common').strip()

GRAPH_BASE    = 'https://graph.microsoft.com/v1.0'
AUTH_BASE      = f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0'
SCOPES        = 'openid profile email Mail.Read Mail.ReadBasic offline_access'

_KV_KEY = 'outlook_tokens'

# ── Invoice keywords (French + English + Arabic) ────────────────────
_INVOICE_KEYWORDS = [
    # French
    'facture', 'bon de commande', 'devis', 'proforma', 'avoir',
    'montant', 'ttc', 'ht', 'tva', 'règlement', 'paiement',
    'échéance', 'fournisseur', 'reçu', 'bordereau',
    # English
    'invoice', 'receipt', 'purchase order', 'quotation', 'payment',
    'amount due', 'billing', 'statement', 'remittance',
    # Arabic
    'فاتورة', 'إيصال', 'دفع', 'مبلغ',
]

_INVOICE_PATTERN = re.compile(
    '|'.join(re.escape(kw) for kw in _INVOICE_KEYWORDS),
    re.IGNORECASE,
)


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


# ═══════════════════════════════════════════════════════════════════
# OAuth2 helpers
# ═══════════════════════════════════════════════════════════════════

def get_auth_url(redirect_uri: str, state: str = '') -> str:
    """Build the Microsoft OAuth2 authorization URL."""
    params = {
        'client_id':     CLIENT_ID,
        'response_type': 'code',
        'redirect_uri':  redirect_uri,
        'scope':         SCOPES,
        'response_mode': 'query',
        'state':         state or uuid.uuid4().hex[:16],
    }
    return f"{AUTH_BASE}/authorize?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> Dict:
    """Exchange authorization code for tokens."""
    r = http.post(f"{AUTH_BASE}/token", data={
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code':          code,
        'redirect_uri':  redirect_uri,
        'grant_type':    'authorization_code',
        'scope':         SCOPES,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def refresh_tokens(refresh_token: str) -> Dict:
    """Refresh an expired access token."""
    r = http.post(f"{AUTH_BASE}/token", data={
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': refresh_token,
        'grant_type':    'refresh_token',
        'scope':         SCOPES,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Token persistence (kv_settings table) ────────────────────────

def save_tokens(tokens: Dict, user_email: str = ''):
    """Store OAuth tokens in Postgres."""
    payload = {
        'access_token':  tokens.get('access_token', ''),
        'refresh_token': tokens.get('refresh_token', ''),
        'expires_at':    (datetime.utcnow() + timedelta(
            seconds=int(tokens.get('expires_in', 3600))
        )).isoformat(),
        'user_email':    user_email or tokens.get('id_token_claims', {}).get('preferred_username', ''),
        'connected_at':  datetime.utcnow().isoformat(),
    }
    db.execute(
        """INSERT INTO kv_settings (key, value, updated_at)
           VALUES (%s, %s::jsonb, %s)
           ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
        (_KV_KEY, json.dumps(payload), datetime.utcnow()),
    )


def load_tokens() -> Optional[Dict]:
    """Load stored tokens. Returns None if not connected."""
    row = db.one("SELECT value FROM kv_settings WHERE key = %s", (_KV_KEY,))
    if row and isinstance(row.get('value'), dict):
        return row['value']
    return None


def clear_tokens():
    """Remove stored tokens (disconnect)."""
    db.execute("DELETE FROM kv_settings WHERE key = %s", (_KV_KEY,))


def get_valid_token() -> Optional[str]:
    """Get a valid access token, refreshing if needed."""
    tokens = load_tokens()
    if not tokens:
        return None

    access_token  = tokens.get('access_token', '')
    refresh_token = tokens.get('refresh_token', '')
    expires_at    = tokens.get('expires_at', '')

    # Check if expired
    try:
        exp = datetime.fromisoformat(expires_at)
        if datetime.utcnow() < exp - timedelta(minutes=5):
            return access_token  # Still valid
    except Exception:
        pass

    # Try refresh
    if refresh_token:
        try:
            new_tokens = refresh_tokens(refresh_token)
            # Preserve refresh_token if not returned
            if 'refresh_token' not in new_tokens:
                new_tokens['refresh_token'] = refresh_token
            save_tokens(new_tokens, tokens.get('user_email', ''))
            return new_tokens.get('access_token', '')
        except Exception:
            pass

    return None


def get_connection_status() -> Dict:
    """Return connection status for the UI."""
    tokens = load_tokens()
    if not tokens:
        return {'connected': False}
    return {
        'connected':    True,
        'email':        tokens.get('user_email', ''),
        'connected_at': tokens.get('connected_at', ''),
    }


# ═══════════════════════════════════════════════════════════════════
# Microsoft Graph email operations
# ═══════════════════════════════════════════════════════════════════

def _graph_get(path: str, token: str, params: Dict = None) -> Dict:
    """Make an authenticated GET to Microsoft Graph."""
    r = http.get(
        f"{GRAPH_BASE}{path}",
        headers={'Authorization': f'Bearer {token}'},
        params=params or {},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_recent_emails(token: str, hours: int = 48, top: int = 50) -> List[Dict]:
    """Fetch recent inbox emails from Microsoft Graph."""
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
    data = _graph_get('/me/mailFolders/inbox/messages', token, {
        '$top':     str(top),
        '$orderby': 'receivedDateTime desc',
        '$filter':  f"receivedDateTime ge {since}",
        '$select':  'id,subject,from,receivedDateTime,bodyPreview,hasAttachments,body',
    })
    return data.get('value', [])


def get_email_attachments(token: str, message_id: str) -> List[Dict]:
    """Get attachments for a specific email."""
    data = _graph_get(f'/me/messages/{message_id}/attachments', token, {
        '$select': 'id,name,contentType,size,contentBytes',
    })
    return data.get('value', [])


def get_attachment_content(token: str, message_id: str, attachment_id: str) -> Tuple[bytes, str, str]:
    """Download a specific attachment. Returns (bytes, filename, content_type)."""
    data = _graph_get(f'/me/messages/{message_id}/attachments/{attachment_id}', token)
    content_b64 = data.get('contentBytes', '')
    filename    = data.get('name', 'attachment')
    content_type = data.get('contentType', 'application/octet-stream')
    return base64.b64decode(content_b64), filename, content_type


# ═══════════════════════════════════════════════════════════════════
# Invoice detection (keyword + Gemini AI)
# ═══════════════════════════════════════════════════════════════════

def _keyword_score(email: Dict) -> float:
    """Quick keyword-based relevance score (0-1). Cheap, no API calls."""
    text = ' '.join([
        email.get('subject', ''),
        email.get('bodyPreview', ''),
    ]).lower()
    matches = _INVOICE_PATTERN.findall(text)
    if not matches:
        return 0.0
    # Score: more matches = higher score, cap at 1.0
    return min(len(matches) / 3.0, 1.0)


def _gemini_classify(email: Dict) -> Dict:
    """Use Gemini to classify whether an email is invoice-related.

    Returns: {is_invoice: bool, confidence: float, reason: str, suggested_fields: {}}
    """
    if not gemini_ok():
        return {'is_invoice': False, 'confidence': 0, 'reason': 'Gemini not configured'}

    subject = email.get('subject', '')
    body    = email.get('bodyPreview', '')[:500]
    sender  = email.get('from', {}).get('emailAddress', {}).get('address', '')
    has_att = email.get('hasAttachments', False)

    prompt = f"""Analyze this email and determine if it contains or references an invoice, bill, receipt, or financial document.

SUBJECT: {subject}
FROM: {sender}
HAS ATTACHMENTS: {has_att}
BODY PREVIEW: {body}

Return ONLY valid JSON (no markdown, no fences):
{{
  "is_invoice": true/false,
  "confidence": 0.0-1.0,
  "reason": "brief explanation",
  "suggested_fields": {{
    "supplier_name": "extracted supplier name or empty",
    "amount": 0.0,
    "currency": "TND/EUR/USD or empty",
    "invoice_ref": "extracted reference or empty"
  }}
}}"""

    try:
        raw = gemini_generate(
            system_prompt="You are a financial email classifier. Return only JSON.",
            user_prompt=prompt,
            temperature=0.1,
            max_tokens=300,
        )
        # Strip code fences if present
        raw = re.sub(r'^```[a-z]*\n?', '', raw.strip())
        raw = re.sub(r'\n?```$', '', raw.strip())
        return json.loads(raw)
    except Exception:
        return {'is_invoice': False, 'confidence': 0, 'reason': 'Classification failed'}


def scan_emails(hours: int = 48) -> List[Dict]:
    """Scan recent emails for invoices. Two-stage: keywords → Gemini.

    Returns a list of detected invoice emails with metadata.
    """
    token = get_valid_token()
    if not token:
        raise RuntimeError("Outlook non connecté. Veuillez vous connecter d'abord.")

    emails = fetch_recent_emails(token, hours=hours)

    # Load already-scanned IDs from DB
    scanned_row = db.one("SELECT value FROM kv_settings WHERE key = 'outlook_scanned'")
    scanned_ids = set()
    if scanned_row and isinstance(scanned_row.get('value'), dict):
        scanned_ids = set(scanned_row['value'].get('ids', []))

    results = []

    for email in emails:
        msg_id = email.get('id', '')
        if msg_id in scanned_ids:
            continue

        # Stage 1: keyword pre-filter
        kw_score = _keyword_score(email)
        if kw_score < 0.1 and not email.get('hasAttachments', False):
            continue  # Skip irrelevant emails without attachments

        # For emails with attachments, lower the bar (might be invoice PDF)
        if kw_score < 0.1 and email.get('hasAttachments', False):
            # Check attachment names for invoice indicators
            att_text = email.get('subject', '').lower()
            if not any(kw in att_text for kw in ['facture', 'invoice', 'reçu', 'receipt', 'devis']):
                kw_score = 0.05  # Very low but still pass to Gemini if has attachments

        if kw_score < 0.05:
            continue

        # Stage 2: Gemini classification
        classification = _gemini_classify(email)

        if classification.get('is_invoice', False) and classification.get('confidence', 0) >= 0.5:
            sender = email.get('from', {}).get('emailAddress', {})
            results.append({
                'email_id':       msg_id,
                'subject':        email.get('subject', ''),
                'sender_name':    sender.get('name', ''),
                'sender_email':   sender.get('address', ''),
                'received_at':    email.get('receivedDateTime', ''),
                'has_attachments': email.get('hasAttachments', False),
                'body_preview':   email.get('bodyPreview', '')[:200],
                'confidence':     classification.get('confidence', 0),
                'reason':         classification.get('reason', ''),
                'suggested':      classification.get('suggested_fields', {}),
                'keyword_score':  kw_score,
            })

        # Mark as scanned
        scanned_ids.add(msg_id)

    # Persist scanned IDs (keep last 500 to avoid unbounded growth)
    scanned_list = list(scanned_ids)[-500:]
    db.execute(
        """INSERT INTO kv_settings (key, value, updated_at)
           VALUES ('outlook_scanned', %s::jsonb, %s)
           ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
        (json.dumps({'ids': scanned_list}), datetime.utcnow()),
    )

    # Sort by confidence descending
    results.sort(key=lambda x: x.get('confidence', 0), reverse=True)
    return results


def import_email_invoice(message_id: str) -> Dict:
    """Download the first PDF/image attachment from an email and process it.

    Returns the invoice_processor result.
    """
    from invoice_processor import process_invoice

    token = get_valid_token()
    if not token:
        raise RuntimeError("Outlook non connecté.")

    attachments = get_email_attachments(token, message_id)

    # Find the first processable attachment (PDF, PNG, JPG)
    valid_exts = ('.pdf', '.png', '.jpg', '.jpeg', '.webp')
    target = None
    for att in attachments:
        name = (att.get('name') or '').lower()
        if any(name.endswith(ext) for ext in valid_exts):
            target = att
            break

    if not target:
        raise ValueError("Aucune pièce jointe traitable trouvée (PDF/PNG/JPG requis).")

    # Download attachment content
    file_bytes, filename, content_type = get_attachment_content(
        token, message_id, target['id']
    )

    # Process through existing invoice processor (Gemini OCR)
    result = process_invoice(file_bytes, filename)
    result['source'] = 'outlook'
    result['source_email_id'] = message_id
    result['original_filename'] = filename

    return result
