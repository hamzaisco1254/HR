"""
Flask application — HR Automated Docs & Invoice Intelligence.

All routes are protected by authentication. Admin can manage up to 5 users.
"""
import io
import json
import logging
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse, parse_qs

# ── Structured server-side logging ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s - %(message)s',
)
logger = logging.getLogger('plw')

from flask import (Flask, render_template, request, send_file,
                   redirect, url_for, jsonify, session)
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_API_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_API_DIR)
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from template_handler import TemplateHandler
from reference_manager import ReferenceManager
from excel_importer import ExcelImporter
from company_manager import CompanyManager
from document_history import DocumentHistory
from models import Employee, Company, DocumentConfig
from financial_store import InvoiceStore, BalanceStore, PaymentStore, ExchangeRates
from invoice_processor import process_invoice, generate_ai_insights
from kpi_engine import compute_all_kpis, get_cashflow_timeseries, get_status_distribution
from auth import UserStore, login_required, admin_required
import email_service
from trips_store import TripsStore, TRIP_DOCS, VISA_DOCS
from client_store import ClientStore
from employee_store import DepartmentStore, EmployeeStore, strip_sensitive
from project_store import ProjectStore
from income_store import IncomeStore
from planned_expense_store import PlannedExpenseStore, get_finance_rates, set_finance_rates
import statements_engine
import forecast_engine
import finance_assistant
from rag_store import RagStore, CATEGORIES as RAG_CATEGORIES
import outlook_agent
from gemini_client import generate as gemini_generate, is_configured as gemini_configured
from rate_limiter import rate_limit
from audit_store import audit_store, audit

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=os.path.join(_API_DIR, 'templates'),
)

# SECRET_KEY must be provided via environment variable.
# If missing, we generate a random ephemeral one and log a loud warning:
# sessions will not survive cold starts, which is a visible symptom that
# forces the operator to set the variable in Vercel.
_secret = os.environ.get('SECRET_KEY', '').strip()
if not _secret:
    import secrets as _sec
    _secret = _sec.token_hex(32)
    print('[SECURITY WARNING] SECRET_KEY env var not set — using ephemeral key. '
          'Sessions will not persist across cold starts. Set SECRET_KEY in Vercel env vars.')
app.secret_key = _secret

app.config['MAX_CONTENT_LENGTH']      = 50 * 1024 * 1024
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE']   = True   # HTTPS only

# ── Security headers on every response ──────────────────────────────────
# CSP note: 'unsafe-inline' is currently required because all templates
# embed large inline <style> and <script> blocks. Future work should move
# them to hashed external files so we can drop 'unsafe-inline'.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)

@app.after_request
def _set_security_headers(resp):
    resp.headers['X-Content-Type-Options']    = 'nosniff'
    resp.headers['X-Frame-Options']           = 'DENY'
    resp.headers['Referrer-Policy']           = 'strict-origin-when-cross-origin'
    resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    resp.headers['Permissions-Policy']        = 'camera=(), microphone=(), geolocation=()'
    resp.headers['Content-Security-Policy']   = _CSP
    return resp


# ── CSRF defense (Origin/Referer check, no tokens) ─────────────────────
# SameSite=Strict cookies already block cross-site POSTs in modern browsers.
# This adds a defense-in-depth layer: every state-changing request MUST
# carry an Origin (or Referer) header that matches the request Host.
_UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

@app.before_request
def _csrf_origin_check():
    if request.method not in _UNSAFE_METHODS:
        return None

    # Allow health/webhook-style endpoints if ever added (none for now)
    origin  = request.headers.get('Origin', '')
    referer = request.headers.get('Referer', '')
    host    = request.host

    def _same_origin(url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            return parsed.netloc == host
        except Exception:
            return False

    if _same_origin(origin) or _same_origin(referer):
        return None

    logger.warning('csrf_blocked method=%s path=%s origin=%r referer=%r',
                   request.method, request.path, origin, referer)
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Origine non autorisée (protection CSRF).'}), 403
    return 'Origine non autorisée', 403

_TMP = tempfile.gettempdir()
_UPLOAD_DIR = os.path.join(_TMP, 'hr_uploads')
os.makedirs(_UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Database bootstrap (idempotent: creates tables if missing, seeds admin)
# ---------------------------------------------------------------------------
import db as _db_module
from db_schema import bootstrap as _db_bootstrap

if _db_module.is_configured():
    try:
        _boot = _db_bootstrap()
        logger.info('db_bootstrap result=%s', _boot)
    except Exception:
        logger.exception('db_bootstrap_failed')
else:
    logger.warning('No DATABASE_URL / POSTGRES_URL set — DB features will fail.')

# ---------------------------------------------------------------------------
# Singleton managers
# ---------------------------------------------------------------------------

ref_mgr = ReferenceManager()
company_mgr = CompanyManager()
history_mgr = DocumentHistory()
template_handler = TemplateHandler()
inv_store = InvoiceStore()
bal_store = BalanceStore()
pay_store = PaymentStore()
fx_rates = ExchangeRates()
user_store = UserStore()
trips_store = TripsStore()
rag_store = RagStore()
client_store     = ClientStore()
department_store = DepartmentStore()
employee_store   = EmployeeStore()
project_store    = ProjectStore()
income_store     = IncomeStore(fx_rates=fx_rates)
planned_store    = PlannedExpenseStore(fx_rates=fx_rates)

# ---------------------------------------------------------------------------
# Cloud URL normalizer (exact copy of desktop)
# ---------------------------------------------------------------------------

def normalize_cloud_excel_url(url: str) -> str:
    u = url.strip()
    if not u:
        return u
    if 'docs.google.com/spreadsheets/d/' in u:
        file_id = u.split('/d/')[1].split('/')[0]
        return f'https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx'
    if 'drive.google.com' in u:
        if '/file/d/' in u:
            file_id = u.split('/file/d/')[1].split('/')[0]
            return f'https://drive.google.com/uc?export=download&id={file_id}'
        if 'open?id=' in u:
            file_id = u.split('open?id=')[1].split('&')[0]
            return f'https://drive.google.com/uc?export=download&id={file_id}'
        if 'uc?id=' in u:
            if 'export=download' in u:
                return u
            sep = '&' if '?' in u else '?'
            return f'{u}{sep}export=download'
    if 'dropbox.com' in u:
        if 'dl=0' in u:
            return u.replace('dl=0', 'dl=1')
        if 'dl=1' not in u:
            sep = '&' if '?' in u else '?'
            return f'{u}{sep}dl=1'
    if '1drv.ms' in u or 'onedrive.live.com' in u:
        if 'download=1' not in u:
            return u + ('&' if '?' in u else '?') + 'download=1'
        return u
    if '-my.sharepoint.com' in u and ':x:/g/personal/' in u:
        base_url = u.split('?')[0]
        return base_url + '?download=1&isSPOFile=1&web=1'
    if 'sharepoint.com' in u:
        if 'download=1' not in u:
            u = u + ('&' if '?' in u else '?') + 'download=1'
        for pattern in [':x:/r/', ':u:/r/', ':w:/r/', ':p:/r/']:
            if pattern in u:
                base = u.split(pattern)[0]
                file_path = u.split(pattern)[1].split('?')[0]
                source_url = f"{base}/{file_path}"
                return f"{base}/_layouts/15/download.aspx?SourceUrl={quote(source_url, safe='')}"
        if '/Shared Documents/' in u and '/_layouts/15/download.aspx' not in u:
            base = u.split('/Shared Documents/')[0]
            filename = u.split('/Shared Documents/')[-1].split('?')[0]
            return f"{base}/_layouts/15/download.aspx?SourceUrl={quote(f'{base}/Shared Documents/{filename}', safe='')}"
        return u
    return u


def download_to_temp_excel(url: str) -> str:
    import requests
    strategies = [
        {'url': url, 'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}},
        {'url': url.split('?')[0] + '?download=1&isSPOFile=1', 'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/octet-stream', 'Referer': 'https://office.com/'}},
        {'url': url, 'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': '*/*'}},
        {'url': url.split('?')[0] + '?download=1&web=1', 'headers': {
            'User-Agent': 'Microsoft Excel', 'Accept': 'application/vnd.ms-excel'}},
    ]
    last_error = None
    for idx, strategy in enumerate(strategies):
        try:
            response = requests.get(strategy['url'], stream=True, timeout=30,
                                    headers=strategy['headers'], allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '').lower()
            content_length = len(response.content) if response.content else 0
            if 'text/html' in content_type:
                last_error = f"Strategy {idx+1}: HTML response"; continue
            if content_length > 100:
                with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False, dir=_UPLOAD_DIR) as tf:
                    tf.write(response.content); temp_path = tf.name
                with open(temp_path, 'rb') as f:
                    sig = f.read(4)
                if sig.startswith(b'PK'):
                    return temp_path
                else:
                    last_error = f"Strategy {idx+1}: Invalid file signature"
                    try: os.unlink(temp_path)
                    except: pass
                    continue
            else:
                last_error = f"Strategy {idx+1}: Response too small ({content_length} bytes)"
        except Exception as e:
            last_error = f"Strategy {idx+1}: {e}"
    raise ValueError(
        "Impossible de télécharger le fichier Excel.\n"
        f"Détail: {last_error}")


# ---------------------------------------------------------------------------
# Template context
# ---------------------------------------------------------------------------

def _ctx(active_tab: str = 'attestation') -> dict:
    company = company_mgr.get_company_data()
    docs = history_mgr.get_all_documents()
    docs_sorted = sorted(docs, key=lambda x: x.get('timestamp', ''), reverse=True)
    today = datetime.now()
    rates = fx_rates.get_all_rates()

    return {
        'active_tab': active_tab,
        'company': company,
        'ref_year': ref_mgr.get_year_prefix(),
        'ref_counter': ref_mgr.get_counter(),
        'ref_next': ref_mgr.get_next_reference(),
        'history': docs_sorted,
        'attestation_count': sum(1 for d in docs if d.get('doc_type') == 'attestation'),
        'mission_count': sum(1 for d in docs if d.get('doc_type') == 'ordre_mission'),
        'now_date': today.strftime('%Y-%m-%d'),
        'week_date': (today + timedelta(days=7)).strftime('%Y-%m-%d'),
        'rates': rates,
        # Auth context
        'user_name': session.get('user_name', ''),
        'user_email': session.get('user_email', ''),
        'user_role': session.get('user_role', ''),
        'users': user_store.get_all_users() if session.get('user_role') == 'admin' else [],
        'max_users': 5,
    }


def _parse_date(s: str, fallback=None):
    if not s:
        return fallback or datetime.now()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return fallback or datetime.now()


def _send_docx(buf: io.BytesIO, filename: str):
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


# ═══════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
@rate_limit(tag='login', max_requests=5, window_seconds=300, by='ip')
def login():
    if session.get('user_id'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user      = user_store.authenticate(email, password)
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr) or ''
        if user:
            session['user_id']    = user['id']
            session['user_email'] = user['email']
            session['user_name']  = user['name']
            session['user_role']  = user['role']
            session.permanent = True
            app.permanent_session_lifetime = timedelta(hours=12)
            logger.info('login_success user=%s ip=%s', user['email'], client_ip)
            audit_store.record(
                action='auth.login', actor_id=user['id'], actor_email=user['email'],
                entity_type='user', entity_id=user['id'],
                ip=client_ip, ok=True,
                details={'force_password_reset': bool(user_store.must_change_password(user['id']))},
            )
            return redirect(url_for('index'))
        logger.warning('login_failed email=%s ip=%s', email, client_ip)
        audit_store.record(
            action='auth.login', actor_id='', actor_email=email,
            entity_type='user', entity_id='',
            ip=client_ip, ok=False,
            details={'reason': 'invalid_credentials'},
        )
        return render_template('login.html', error='Email ou mot de passe incorrect.')

    return render_template('login.html', error=None)


@app.route('/logout')
def logout():
    uid   = session.get('user_id', '')
    email = session.get('user_email', '')
    if uid:
        audit_store.record(
            action='auth.logout', actor_id=uid, actor_email=email,
            entity_type='user', entity_id=uid,
            ip=request.headers.get('X-Forwarded-For', request.remote_addr) or '', ok=True,
        )
    session.clear()
    return redirect(url_for('login'))


# ═══════════════════════════════════════════════════════════════════
# AUTH API ENDPOINTS (admin only)
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/auth/users')
@admin_required
def api_auth_users():
    from auth import MAX_USERS as _MAX
    return jsonify({'users': user_store.get_all_users(), 'max': _MAX})


@app.route('/admin/audit')
@admin_required
def admin_audit_page():
    return render_template('audit.html')


@app.route('/api/audit/list')
@admin_required
def api_audit_list():
    """Return recent audit log entries (admin only)."""
    limit = min(int(request.args.get('limit', 100) or 100), 500)
    action_filter = (request.args.get('action') or '').strip()
    return jsonify({
        'entries': audit_store.list_recent(limit=limit, action_filter=action_filter),
        'stats':   audit_store.stats(),
    })


@app.route('/api/auth/add_user', methods=['POST'])
@admin_required
@audit(action='user.create', entity='user', entity_arg='email')
def api_auth_add_user():
    """Create a user account.

    If `password` is empty/missing, the server generates a secure temp
    password and emails it to the user. The user is forced to change
    the password on first login. If SMTP is not configured, the plain
    password is returned in the response so the admin can communicate
    it manually (one-time only).
    """
    data = request.get_json(force=True) or {}
    try:
        user, plain_password = user_store.add_user(
            email=data.get('email', ''),
            password=data.get('password', ''),  # empty => auto-generated
            name=data.get('name', ''),
            role=(data.get('role') or 'user'),
            force_password_reset=True,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # Send credentials email
    send_status = {'sent': False, 'reason': 'send_not_attempted'}
    if email_service.is_configured():
        send_status = email_service.send_credentials(
            to_email=user['email'],
            name=user['name'],
            password=plain_password,
            role=user['role'],
            is_resend=False,
        )
        if send_status.get('sent'):
            user_store.mark_credentials_sent(user['id'])
            audit_store.record(
                action='user.credentials_emailed', actor_id=session.get('user_id', ''),
                actor_email=session.get('user_email', ''),
                entity_type='user', entity_id=user['id'],
                ip=request.headers.get('X-Forwarded-For', request.remote_addr) or '', ok=True,
            )

    response = {
        'status': 'ok',
        'user':   user_store.get_user_by_id(user['id']) or user,
        'email_sent':   bool(send_status.get('sent')),
        'email_reason': send_status.get('reason'),
        'smtp_configured': email_service.is_configured(),
    }
    # Only reveal the plain password when the email did NOT succeed.
    # This is a fallback so the admin can communicate it out-of-band.
    if not send_status.get('sent'):
        response['plain_password'] = plain_password
        response['plain_password_warning'] = (
            "L'email n'a pas pu etre envoye. Communiquez ce mot de passe "
            "manuellement et de maniere securisee — il ne sera plus affiche."
        )
    return jsonify(response)


@app.route('/api/auth/resend_credentials', methods=['POST'])
@admin_required
@audit(action='user.resend_credentials', entity='user', entity_arg='id')
def api_auth_resend_credentials():
    """Regenerate a temp password for an existing user and email it.

    Always rotates the password (no 'just re-send the same one' option,
    because we don't store plaintexts). The user must change it again
    on next login.
    """
    data = request.get_json(force=True) or {}
    uid = (data.get('id') or '').strip()
    user, plain_password = user_store.reset_password(uid)
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404

    send_status = {'sent': False, 'reason': 'send_not_attempted'}
    if email_service.is_configured():
        send_status = email_service.send_credentials(
            to_email=user['email'], name=user['name'],
            password=plain_password, role=user['role'], is_resend=True,
        )
        if send_status.get('sent'):
            user_store.mark_credentials_sent(uid)

    response = {
        'status': 'ok',
        'user':   user_store.get_user_by_id(uid) or user,
        'email_sent':   bool(send_status.get('sent')),
        'email_reason': send_status.get('reason'),
        'smtp_configured': email_service.is_configured(),
    }
    if not send_status.get('sent'):
        response['plain_password'] = plain_password
        response['plain_password_warning'] = (
            "L'email n'a pas pu etre envoye. Communiquez ce mot de passe "
            "manuellement — il ne sera plus affiche."
        )
    return jsonify(response)


@app.route('/api/auth/remove_user', methods=['POST'])
@admin_required
@audit(action='user.delete', entity='user', entity_arg='id')
def api_auth_remove_user():
    data = request.get_json(force=True)
    try:
        if user_store.remove_user(data.get('id', '')):
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/auth/toggle_user', methods=['POST'])
@admin_required
@audit(action='user.toggle', entity='user', entity_arg='id')
def api_auth_toggle_user():
    data = request.get_json(force=True)
    try:
        result = user_store.toggle_user(data.get('id', ''))
        if result:
            return jsonify({'status': 'ok', 'user': result})
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/auth/change_password', methods=['POST'])
@login_required
@audit(action='user.change_password', entity='user', entity_arg='id')
def api_auth_change_password():
    """Change the password of a user.

    - A user changing their OWN password must provide the current one
      (unless they are flagged force_password_reset — first login flow).
    - An admin changing someone else's password does not need the current
      password but the target must change theirs again on next login.
    """
    data = request.get_json(force=True) or {}
    target_id = (data.get('id') or session.get('user_id') or '').strip()
    is_self = (target_id == session.get('user_id'))
    is_admin = (session.get('user_role') == 'admin')

    if not is_admin and not is_self:
        return jsonify({'error': 'Non autorisé'}), 403

    new_password = data.get('password', '')
    if not new_password:
        return jsonify({'error': 'Nouveau mot de passe requis.'}), 400

    # Self-change: require current password unless force-reset is set
    if is_self:
        must_force = user_store.must_change_password(target_id)
        current_pw = data.get('current_password', '')
        if not must_force:
            if not current_pw:
                return jsonify({'error': 'Mot de passe actuel requis.'}), 400
            if not user_store.verify_current_password(target_id, current_pw):
                return jsonify({'error': 'Mot de passe actuel incorrect.'}), 403

    # Admin changing someone else's password → mark force-reset so the
    # target must change it again on next login (we just gave them a
    # password they didn't choose).
    if is_admin and not is_self:
        try:
            if not user_store.change_password(target_id, new_password):
                return jsonify({'error': 'Utilisateur non trouvé'}), 404
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        # Re-flag force_password_reset since admin set this
        db.execute("UPDATE users SET force_password_reset = TRUE WHERE id = %s", (target_id,))
        return jsonify({'status': 'ok'})

    # Self-change path
    try:
        if user_store.change_password(target_id, new_password):
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/auth/me')
@login_required
def api_auth_me():
    """Return the current user with force_password_reset flag for the UI."""
    uid = session.get('user_id', '')
    user = user_store.get_user_by_id(uid) or {}
    return jsonify({
        'user':                  user,
        'force_password_reset':  bool(user.get('force_password_reset')),
        'smtp_configured':       email_service.is_configured(),
    })


@app.route('/api/auth/smtp_status')
@admin_required
def api_auth_smtp_status():
    return jsonify(email_service.status())


@app.route('/api/auth/smtp_test', methods=['POST'])
@admin_required
@rate_limit(tag='smtp_test', max_requests=3, window_seconds=300, by='user')
@audit(action='smtp.test', entity='smtp')
def api_auth_smtp_test():
    data = request.get_json(force=True) or {}
    to_email = (data.get('to') or session.get('user_email') or '').strip()
    if not to_email or '@' not in to_email:
        return jsonify({'error': 'Email destinataire invalide.'}), 400
    result = email_service.send_test(to_email)
    if result.get('sent'):
        return jsonify({'status': 'ok'})
    return jsonify({'error': f"Echec: {result.get('reason', 'unknown')}",
                    'detail': result.get('detail', '')}), 502


# ═══════════════════════════════════════════════════════════════════
# STATIC ASSETS
# ═══════════════════════════════════════════════════════════════════

@app.route('/static/logo.png')
def serve_logo():
    path = os.path.join(_ROOT_DIR, 'templates', 'logo.png')
    if os.path.exists(path):
        return send_file(path, mimetype='image/png')
    return '', 404


# ═══════════════════════════════════════════════════════════════════
# MAIN PAGE ROUTES (all protected)
# ═══════════════════════════════════════════════════════════════════

@app.route('/')
@login_required
def index():
    return redirect(url_for('attestation'))


@app.route('/attestation', methods=['GET', 'POST'])
@login_required
def attestation():
    if request.method == 'POST':
        try:
            company = Company(
                name=request.form.get('company_name', ''),
                legal_id=request.form.get('legal_id', ''),
                representative_name=request.form.get('representative', ''),
                address=request.form.get('address', ''),
                city=request.form.get('city', ''),
            )
            employee = Employee(
                name=request.form.get('employee_name', '').strip(),
                gender=request.form.get('gender', 'Monsieur'),
                cin=request.form.get('cin', ''),
                cin_place=request.form.get('cin_place', 'Tunis') or 'Tunis',
                cin_date=_parse_date(request.form.get('cin_date', ''), datetime(2020, 1, 1)),
                position=request.form.get('position', ''),
                start_date=_parse_date(request.form.get('start_date', ''), datetime.now()),
            )
            # Auto-increment reference (year prefix from form if explicitly set)
            year_from_form = request.form.get('ref_year', '')
            if year_from_form:
                ref_mgr.set_year_prefix(int(year_from_form))
            reference = ref_mgr.generate_reference()
            config = DocumentConfig(employee=employee, company=company,
                                    reference=reference, document_type='attestation')
            buf = template_handler.create_attestation_document(config)
            safe_name = employee.name.replace(' ', '_').replace('/', '_')
            filename = f"Attestation_{safe_name}_{reference.replace('/', '-')}.docx"
            history_mgr.add_document(reference=reference, doc_type='attestation',
                                     employee_name=employee.name, file_path=filename)
            return _send_docx(buf, filename)
        except Exception:
            logger.exception('attestation_generation_failed')
            return jsonify({'error': "Impossible de generer l'attestation. Verifiez les donnees saisies."}), 400
    return render_template('app.html', **_ctx('attestation'))


@app.route('/ordre_mission', methods=['GET', 'POST'])
@login_required
def ordre_mission():
    if request.method == 'POST':
        try:
            company = Company(
                name=company_mgr.get_field('name'),
                legal_id=company_mgr.get_field('legal_id'),
                representative_name=company_mgr.get_field('representative_name'),
                address=company_mgr.get_field('address'),
                city=company_mgr.get_field('city'),
            )
            birth_date = _parse_date(request.form.get('birth_date', ''), datetime(1990, 1, 1))
            mission_start = _parse_date(request.form.get('mission_start_date', ''), datetime.now())
            mission_end = _parse_date(request.form.get('mission_end_date', ''), mission_start)
            employee = Employee(
                name=request.form.get('employee_name', '').strip(),
                gender=request.form.get('gender', 'Monsieur'),
                cin=request.form.get('cin', ''),
                cin_place=request.form.get('birth_place', 'Tunis') or 'Tunis',
                cin_date=birth_date,
                position=request.form.get('position', ''),
                start_date=mission_start,
            )
            reference = ref_mgr.generate_reference()
            config = DocumentConfig(employee=employee, company=company,
                                    reference=reference, document_type='ordre_mission')
            buf = template_handler.create_ordre_mission_document(
                config, country=request.form.get('country', 'Allemagne'),
                nationality=request.form.get('nationality', ''),
                address=request.form.get('emp_address', ''),
                mission_end_date=mission_end,
            )
            safe_name = employee.name.replace(' ', '_').replace('/', '_')
            filename = f"Ordre_{safe_name}_{reference.replace('/', '-')}.docx"
            history_mgr.add_document(reference=reference, doc_type='ordre_mission',
                                     employee_name=employee.name, file_path=filename)
            return _send_docx(buf, filename)
        except Exception:
            logger.exception('ordre_mission_generation_failed')
            return jsonify({'error': "Impossible de generer l'ordre de mission. Verifiez les donnees saisies."}), 400
    return render_template('app.html', **_ctx('ordre_mission'))


@app.route('/historique')
@login_required
def historique():
    return render_template('app.html', **_ctx('historique'))


@app.route('/dashboard')
@login_required
def dashboard_page():
    return render_template('app.html', **_ctx('overview'))


# ═══════════════════════════════════════════════════════════════════
# LEGACY COMPAT
# ═══════════════════════════════════════════════════════════════════

@app.route('/references')
@login_required
def references():
    return redirect(url_for('index'))

@app.route('/reset_references', methods=['POST'])
@login_required
def reset_references_legacy():
    ref_mgr.reset_counter()
    return redirect(url_for('index'))


# ═══════════════════════════════════════════════════════════════════
# HR AJAX ENDPOINTS (all protected)
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/load_excel', methods=['POST'])
@login_required
def api_load_excel():
    importer = ExcelImporter()
    tmp_path = None
    try:
        uploaded = request.files.get('excel_file')
        url = (request.form.get('excel_url') or '').strip()
        if uploaded and uploaded.filename:
            safe_name = secure_filename(uploaded.filename) or 'upload.xlsx'
            if not safe_name.lower().endswith(('.xlsx', '.xls')):
                return jsonify({'error': 'Format non supporte (xlsx/xls uniquement)'}), 400
            tmp_path = os.path.join(_UPLOAD_DIR, safe_name)
            uploaded.save(tmp_path)
        elif url:
            normalized = normalize_cloud_excel_url(url)
            tmp_path = download_to_temp_excel(normalized)
        else:
            return jsonify({'error': 'Aucun fichier ni URL fourni'}), 400
        if not importer.load_excel(tmp_path):
            return jsonify({'error': 'Impossible de charger le fichier Excel'}), 400
        columns = importer.get_columns()
        rows = importer.get_all_rows()
        id_col = None
        for col in columns:
            if col.lower() in ['id', 'employee_id', 'cin']:
                id_col = col; break
        if not id_col:
            return jsonify({'error': "Aucune colonne 'ID' trouvée."}), 400
        employees = []
        for row in rows:
            emp_id = str(row.get(id_col, '')).strip()
            if not emp_id: continue
            normalised = {str(k).lower(): (str(v) if v is not None else '') for k, v in row.items()}
            employees.append({'id': emp_id, 'data': normalised})
        return jsonify({'status': 'ok', 'id_column': id_col, 'count': len(employees), 'employees': employees})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception('excel_load_failed')
        return jsonify({'error': 'Erreur lors du chargement du fichier Excel.'}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except: pass


@app.route('/api/save_company', methods=['POST'])
@login_required
def api_save_company():
    data = request.get_json(force=True)
    company_mgr.set_company_data(
        name=data.get('name', ''), legal_id=data.get('legal_id', ''),
        representative=data.get('representative_name', ''),
        address=data.get('address', ''), city=data.get('city', ''))
    return jsonify({'status': 'ok'})


@app.route('/api/set_reference', methods=['POST'])
@login_required
def api_set_reference():
    data = request.get_json(force=True)
    ref_mgr.set_year_prefix(int(data.get('year_prefix', ref_mgr.get_year_prefix())))
    ref_mgr.set_counter(int(data.get('counter', ref_mgr.get_counter())))
    return jsonify({'next': ref_mgr.get_next_reference(),
                    'year_prefix': ref_mgr.get_year_prefix(),
                    'counter': ref_mgr.get_counter()})


@app.route('/api/reference_info', methods=['GET'])
@login_required
def api_reference_info():
    """Read-only fetch of the current reference state.

    Use this from the UI after a successful document generation to
    refresh the displayed counter without risking overwriting the
    server-side value with a stale form input. The server is the only
    place that increments the counter (in generate_reference()).
    """
    return jsonify({
        'next':        ref_mgr.get_next_reference(),
        'year_prefix': ref_mgr.get_year_prefix(),
        'counter':     ref_mgr.get_counter(),
    })


@app.route('/api/reset_reference', methods=['POST'])
@login_required
def api_reset_reference():
    ref_mgr.reset_counter()
    return jsonify({'next': ref_mgr.get_next_reference(), 'counter': ref_mgr.get_counter()})


@app.route('/api/history')
@login_required
def api_history():
    ft = request.args.get('type', 'all')
    if ft == 'attestation': docs = history_mgr.get_documents_by_type('attestation')
    elif ft == 'ordre_mission': docs = history_mgr.get_documents_by_type('ordre_mission')
    else: docs = history_mgr.get_all_documents()
    return jsonify({'documents': sorted(docs, key=lambda x: x.get('timestamp', ''), reverse=True)})


@app.route('/api/clear_history', methods=['POST'])
@login_required
def api_clear_history():
    history_mgr.clear_history()
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════════════
# FINANCIAL DASHBOARD API (all protected)
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/dashboard/data')
@login_required
def api_dashboard_data():
    invoices = inv_store.get_all()
    accounts = bal_store.get_all()
    total_inv = len(invoices)
    paid = [i for i in invoices if i.get('payment_status') == 'paid']
    unpaid = [i for i in invoices if i.get('payment_status') == 'unpaid']
    overdue = [i for i in invoices if i.get('payment_status') == 'overdue']

    def _sum(lst): return sum(float(i.get('amount', 0)) for i in lst)
    def _fmt(v):
        if abs(v) >= 1_000_000: return f"{v/1_000_000:,.1f}M"
        if abs(v) >= 1_000: return f"{v/1_000:,.1f}K"
        return f"{v:,.2f}"

    total_amt = _sum(invoices)
    net_cash = sum(fx_rates.to_tnd(float(a.get('balance', 0)), a.get('currency', 'TND')) for a in accounts)

    stats = {
        'total_invoices': total_inv, 'total_amount_formatted': f"{_fmt(total_amt)} (multi-devises)",
        'paid_count': len(paid), 'paid_amount_formatted': _fmt(_sum(paid)),
        'unpaid_count': len(unpaid), 'unpaid_amount_formatted': _fmt(_sum(unpaid)),
        'overdue_count': len(overdue), 'overdue_amount_formatted': _fmt(_sum(overdue)),
        'net_cash_formatted': f"{_fmt(net_cash)} TND",
    }
    cashflow = get_cashflow_timeseries(inv_store, pay_store)
    status_dist = get_status_distribution(inv_store)
    supplier_totals = inv_store.total_by_supplier()
    currency_dist = {}
    for inv in invoices:
        c = inv.get('currency', 'TND')
        currency_dist[c] = currency_dist.get(c, 0) + float(inv.get('amount', 0))
    kpis = compute_all_kpis(inv_store, bal_store, pay_store, fx_rates)
    ai_insight = None
    try: ai_insight = generate_ai_insights(invoices, accounts, kpis)
    except: pass
    return jsonify({
        'stats': stats, 'cashflow': cashflow, 'status_dist': status_dist,
        'supplier_totals': supplier_totals, 'currency_dist': currency_dist,
        'kpis': kpis, 'ai_insight': ai_insight, 'overdue_invoices': overdue[:10],
    })


@app.route('/api/invoice/upload', methods=['POST'])
@login_required
@rate_limit(tag='invoice_upload', max_requests=20, window_seconds=600, by='user')
def api_invoice_upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Aucun fichier fourni'}), 400
    safe_name = secure_filename(f.filename) or 'invoice'
    if not safe_name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg', '.webp')):
        return jsonify({'error': 'Format non supporte (pdf/png/jpg/webp uniquement)'}), 400
    file_bytes = f.read()
    result = process_invoice(file_bytes, safe_name)
    return jsonify(result)


@app.route('/api/invoice/create', methods=['POST'])
@login_required
def api_invoice_create():
    data = request.get_json(force=True)
    if not data.get('supplier_name'):
        return jsonify({'error': 'Fournisseur requis'}), 400
    inv = inv_store.add_invoice(data)
    return jsonify({'status': 'ok', 'invoice': inv})


@app.route('/api/invoice/list')
@login_required
def api_invoice_list():
    filters = {k: v for k, v in {
        'status': request.args.get('status'),
        'currency': request.args.get('currency'),
        'supplier': request.args.get('supplier'),
        'date_from': request.args.get('date_from'),
        'date_to': request.args.get('date_to'),
    }.items() if v}
    return jsonify({'invoices': inv_store.get_all(filters or None)})


@app.route('/api/invoice/update', methods=['POST'])
@login_required
@audit(action='invoice.update', entity='invoice', entity_arg='id')
def api_invoice_update():
    data = request.get_json(force=True)
    inv_id = data.pop('id', None)
    if not inv_id: return jsonify({'error': 'ID manquant'}), 400
    result = inv_store.update_invoice(inv_id, data)
    if result is None: return jsonify({'error': 'Facture non trouvée'}), 404
    return jsonify({'status': 'ok', 'invoice': result})


@app.route('/api/invoice/delete', methods=['POST'])
@login_required
@audit(action='invoice.delete', entity='invoice', entity_arg='id')
def api_invoice_delete():
    data = request.get_json(force=True)
    if inv_store.delete_invoice(data.get('id', '')):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Facture non trouvée'}), 404


@app.route('/api/invoice/categories', methods=['GET'])
@login_required
def api_invoice_categories():
    """Return the canonical category code → French label map for UI dropdowns."""
    from invoice_processor import INVOICE_CATEGORIES
    return jsonify({
        'categories': [
            {'code': code, 'label': label}
            for code, label, _hint in INVOICE_CATEGORIES
        ],
    })


@app.route('/api/invoice/classify', methods=['POST'])
@login_required
@rate_limit(tag='invoice_classify', max_requests=30, window_seconds=60, by='user')
@audit(action='invoice.classify', entity='invoice', entity_arg='id')
def api_invoice_classify():
    """Ask Gemini to (re)classify an existing invoice from its metadata.

    The AI suggestion is persisted automatically (overwriting any existing
    category). If the caller wants to review before saving, they can call
    the upload-extraction flow instead.
    """
    from invoice_processor import classify_existing_invoice
    data = request.get_json(force=True) or {}
    inv_id = (data.get('id') or '').strip()
    inv = inv_store.get_by_id(inv_id)
    if not inv:
        return jsonify({'error': 'Facture non trouvée'}), 404
    result = classify_existing_invoice(inv)
    if result.get('error'):
        return jsonify({'error': result['error']}), 503 if not result.get('ai_used') else 502
    if not result.get('category'):
        return jsonify({'error': 'Catégorie non déterminée.'}), 422
    updated = inv_store.update_invoice(inv_id, {'category': result['category']})
    return jsonify({
        'status':     'ok',
        'invoice':    updated,
        'category':   result['category'],
        'confidence': result.get('confidence', 0.0),
    })


@app.route('/api/finance/balances')
@login_required
def api_finance_balances():
    return jsonify({'accounts': bal_store.get_all()})

@app.route('/api/finance/balance', methods=['POST'])
@login_required
def api_finance_update_balance():
    data = request.get_json(force=True)
    result = bal_store.update_balance(data.get('id', ''), float(data.get('balance', 0)))
    if result is None: return jsonify({'error': 'Compte non trouvé'}), 404
    return jsonify({'status': 'ok', 'account': result})

@app.route('/api/finance/add_account', methods=['POST'])
@login_required
def api_finance_add_account():
    data = request.get_json(force=True)
    acc = bal_store.add_account(name=data.get('name', ''), acc_type=data.get('type', 'bank'),
                                currency=data.get('currency', 'TND'),
                                balance=float(data.get('balance', 0)))
    return jsonify({'status': 'ok', 'account': acc})

@app.route('/api/finance/delete_account', methods=['POST'])
@login_required
def api_finance_delete_account():
    data = request.get_json(force=True)
    if bal_store.delete_account(data.get('id', '')):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Compte non trouvé'}), 404

@app.route('/api/finance/exchange_rate', methods=['POST'])
@login_required
def api_finance_exchange_rate():
    data = request.get_json(force=True)
    if 'eur_to_tnd' in data: fx_rates.set_rate('EUR', 'TND', float(data['eur_to_tnd']))
    if 'tnd_to_eur' in data: fx_rates.set_rate('TND', 'EUR', float(data['tnd_to_eur']))
    return jsonify({'status': 'ok', 'rates': fx_rates.get_all_rates()})

@app.route('/api/ai/status')
@login_required
def api_ai_status():
    return jsonify({'available': gemini_configured()})


# ═══════════════════════════════════════════════════════════════════
# ORGANIZATION FOUNDATION API (PR-1)
# Clients, Departments, Employees, Projects, Project assignments.
# All endpoints require login. Mutating endpoints require admin role.
# Salary fields are filtered out for non-admin readers.
# ═══════════════════════════════════════════════════════════════════

def _is_admin() -> bool:
    return session.get('user_role') == 'admin'

def _current_user_id() -> str:
    return session.get('user_id') or ''


# ── Clients ────────────────────────────────────────────────────────

@app.route('/api/finance/clients', methods=['GET'])
@login_required
def api_clients_list():
    include_inactive = request.args.get('include_inactive', '0') in ('1', 'true', 'yes')
    return jsonify({'clients': client_store.list_clients(include_inactive=include_inactive)})


@app.route('/api/finance/clients', methods=['POST'])
@login_required
@admin_required
@audit(action='org.client.create', entity='client')
def api_clients_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'status': 'ok', 'client': client_store.add(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/clients/update', methods=['POST'])
@login_required
@admin_required
@audit(action='org.client.update', entity='client', entity_arg='id')
def api_clients_update():
    data = request.get_json(force=True) or {}
    cid = (data.pop('id', '') or '').strip()
    try:
        result = client_store.update(cid, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Client introuvable'}), 404
    return jsonify({'status': 'ok', 'client': result})


@app.route('/api/finance/clients/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='org.client.delete', entity='client', entity_arg='id')
def api_clients_delete():
    data = request.get_json(force=True) or {}
    cid = (data.get('id') or '').strip()
    if client_store.has_projects(cid):
        return jsonify({'error': 'Ce client est lie a des projets. Desactivez-le ou supprimez ses projets d\'abord.'}), 409
    if not client_store.delete(cid):
        return jsonify({'error': 'Client introuvable'}), 404
    return jsonify({'status': 'ok'})


@app.route('/api/finance/clients/deactivate', methods=['POST'])
@login_required
@admin_required
@audit(action='org.client.deactivate', entity='client', entity_arg='id')
def api_clients_deactivate():
    data = request.get_json(force=True) or {}
    if not client_store.deactivate((data.get('id') or '').strip()):
        return jsonify({'error': 'Client introuvable'}), 404
    return jsonify({'status': 'ok'})


# ── Departments ────────────────────────────────────────────────────

@app.route('/api/finance/departments', methods=['GET'])
@login_required
def api_departments_list():
    return jsonify({'departments': department_store.list_departments()})


@app.route('/api/finance/departments', methods=['POST'])
@login_required
@admin_required
@audit(action='org.department.create', entity='department')
def api_departments_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'status': 'ok', 'department': department_store.add(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/departments/update', methods=['POST'])
@login_required
@admin_required
@audit(action='org.department.update', entity='department', entity_arg='id')
def api_departments_update():
    data = request.get_json(force=True) or {}
    did = (data.pop('id', '') or '').strip()
    try:
        result = department_store.update(did, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Departement introuvable'}), 404
    return jsonify({'status': 'ok', 'department': result})


@app.route('/api/finance/departments/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='org.department.delete', entity='department', entity_arg='id')
def api_departments_delete():
    data = request.get_json(force=True) or {}
    if not department_store.delete((data.get('id') or '').strip()):
        return jsonify({'error': 'Departement introuvable'}), 404
    return jsonify({'status': 'ok'})


# ── Employees ─────────────────────────────────────────────────────

@app.route('/api/finance/employees', methods=['GET'])
@login_required
def api_employees_list():
    include_inactive = request.args.get('include_inactive', '0') in ('1', 'true', 'yes')
    department_id = request.args.get('department_id') or None
    rows = employee_store.list_employees(include_inactive=include_inactive, department_id=department_id)
    if not _is_admin():
        rows = [strip_sensitive(r) for r in rows]
    return jsonify({'employees': rows, 'can_see_salary': _is_admin()})


@app.route('/api/finance/employees', methods=['POST'])
@login_required
@admin_required
@audit(action='org.employee.create', entity='employee')
def api_employees_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'status': 'ok', 'employee': employee_store.add(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/employees/update', methods=['POST'])
@login_required
@admin_required
@audit(action='org.employee.update', entity='employee', entity_arg='id')
def api_employees_update():
    data = request.get_json(force=True) or {}
    eid = (data.pop('id', '') or '').strip()
    try:
        result = employee_store.update(eid, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Employe introuvable'}), 404
    return jsonify({'status': 'ok', 'employee': result})


@app.route('/api/finance/employees/deactivate', methods=['POST'])
@login_required
@admin_required
@audit(action='org.employee.deactivate', entity='employee', entity_arg='id')
def api_employees_deactivate():
    data = request.get_json(force=True) or {}
    if not employee_store.deactivate((data.get('id') or '').strip()):
        return jsonify({'error': 'Employe introuvable'}), 404
    return jsonify({'status': 'ok'})


@app.route('/api/finance/employees/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='org.employee.delete', entity='employee', entity_arg='id')
def api_employees_delete():
    data = request.get_json(force=True) or {}
    if not employee_store.delete((data.get('id') or '').strip()):
        return jsonify({'error': 'Employe introuvable'}), 404
    return jsonify({'status': 'ok'})


# ── Projects ──────────────────────────────────────────────────────

@app.route('/api/finance/projects', methods=['GET'])
@login_required
def api_projects_list():
    status = request.args.get('status') or None
    client_id = request.args.get('client_id') or None
    return jsonify({'projects': project_store.list_projects(status=status, client_id=client_id)})


@app.route('/api/finance/projects', methods=['POST'])
@login_required
@admin_required
@audit(action='org.project.create', entity='project')
def api_projects_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'status': 'ok', 'project': project_store.add(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/projects/update', methods=['POST'])
@login_required
@admin_required
@audit(action='org.project.update', entity='project', entity_arg='id')
def api_projects_update():
    data = request.get_json(force=True) or {}
    pid = (data.pop('id', '') or '').strip()
    try:
        result = project_store.update(pid, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Projet introuvable'}), 404
    return jsonify({'status': 'ok', 'project': result})


@app.route('/api/finance/projects/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='org.project.delete', entity='project', entity_arg='id')
def api_projects_delete():
    data = request.get_json(force=True) or {}
    if not project_store.delete((data.get('id') or '').strip()):
        return jsonify({'error': 'Projet introuvable'}), 404
    return jsonify({'status': 'ok'})


# ── Project assignments ───────────────────────────────────────────

@app.route('/api/finance/projects/<pid>/assignments', methods=['GET'])
@login_required
def api_assignments_list(pid: str):
    return jsonify({'assignments': project_store.list_assignments(project_id=pid)})


@app.route('/api/finance/projects/<pid>/assignments', methods=['POST'])
@login_required
@admin_required
@audit(action='org.assignment.create', entity='project_assignment')
def api_assignments_create(pid: str):
    data = request.get_json(force=True) or {}
    data['project_id'] = pid
    try:
        return jsonify({'status': 'ok', 'assignment': project_store.add_assignment(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/assignments/update', methods=['POST'])
@login_required
@admin_required
@audit(action='org.assignment.update', entity='project_assignment', entity_arg='id')
def api_assignments_update():
    data = request.get_json(force=True) or {}
    aid = (data.pop('id', '') or '').strip()
    try:
        result = project_store.update_assignment(aid, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Affectation introuvable'}), 404
    return jsonify({'status': 'ok', 'assignment': result})


@app.route('/api/finance/assignments/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='org.assignment.delete', entity='project_assignment', entity_arg='id')
def api_assignments_delete():
    data = request.get_json(force=True) or {}
    if not project_store.delete_assignment((data.get('id') or '').strip()):
        return jsonify({'error': 'Affectation introuvable'}), 404
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════════════
# INCOME MANAGEMENT API (PR-4)
# Tracks revenue from outgoing customer invoices. EUR→TND conversion
# is computed at create time using the shared ExchangeRates singleton.
# All endpoints require login; mutations require admin.
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/finance/income', methods=['GET'])
@login_required
def api_income_list():
    filters = {
        'status':        request.args.get('status') or None,
        'client_id':     request.args.get('client_id') or None,
        'department_id': request.args.get('department_id') or None,
        'project_id':    request.args.get('project_id') or None,
        'period_from':   request.args.get('period_from') or None,
        'period_to':     request.args.get('period_to') or None,
        'year':          request.args.get('year') or None,
    }
    return jsonify({'income': income_store.list({k: v for k, v in filters.items() if v})})


@app.route('/api/finance/income', methods=['POST'])
@login_required
@admin_required
@audit(action='income.create', entity='customer_invoice')
def api_income_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'status': 'ok', 'invoice': income_store.add(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/income/update', methods=['POST'])
@login_required
@admin_required
@audit(action='income.update', entity='customer_invoice', entity_arg='id')
def api_income_update():
    data = request.get_json(force=True) or {}
    iid = (data.pop('id', '') or '').strip()
    try:
        result = income_store.update(iid, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Revenu introuvable'}), 404
    return jsonify({'status': 'ok', 'invoice': result})


@app.route('/api/finance/income/mark_paid', methods=['POST'])
@login_required
@admin_required
@audit(action='income.mark_paid', entity='customer_invoice', entity_arg='id')
def api_income_mark_paid():
    data = request.get_json(force=True) or {}
    iid = (data.get('id') or '').strip()
    try:
        result = income_store.mark_paid(
            iid,
            received_tnd=data.get('received_tnd'),
            paid_at=data.get('paid_at'),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Revenu introuvable'}), 404
    return jsonify({'status': 'ok', 'invoice': result})


@app.route('/api/finance/income/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='income.delete', entity='customer_invoice', entity_arg='id')
def api_income_delete():
    data = request.get_json(force=True) or {}
    if not income_store.delete((data.get('id') or '').strip()):
        return jsonify({'error': 'Revenu introuvable'}), 404
    return jsonify({'status': 'ok'})


@app.route('/api/finance/income/kpis', methods=['GET'])
@login_required
def api_income_kpis():
    year = request.args.get('year')
    try:
        year_int = int(year) if year else None
    except (TypeError, ValueError):
        year_int = None
    return jsonify({
        'monthly':       income_store.monthly_summary(year_int),
        'by_department': income_store.by_department(year_int),
        'by_client':     income_store.by_client(year_int),
        'overdue':       income_store.overdue(),
        'fx_rates':      fx_rates.get_all_rates(),
    })


@app.route('/api/finance/income/upload', methods=['POST'])
@login_required
@admin_required
@rate_limit(tag='income_upload', max_requests=20, window_seconds=600, by='user')
def api_income_upload():
    """Upload a customer-invoice PDF, run AI extraction, return fields.

    Reuses the existing vendor-invoice OCR pipeline. The caller then
    POSTs to /api/finance/income to actually persist the row, optionally
    after the user reviews & overrides extracted fields.
    """
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Aucun fichier fourni'}), 400
    safe_name = secure_filename(f.filename) or 'invoice'
    if not safe_name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg', '.webp')):
        return jsonify({'error': 'Format non supporte (PDF/PNG/JPG attendu).'}), 400
    blob = f.read()
    if len(blob) > 10 * 1024 * 1024:
        return jsonify({'error': 'Fichier trop volumineux (max 10 Mo).'}), 400

    try:
        ai = process_invoice(blob, safe_name)
    except Exception as e:
        logger.exception('income_upload_ai_failed')
        return jsonify({'error': f'Echec de l\'extraction IA: {e}'}), 502

    return jsonify({
        'status':            'ok',
        'pdf_filename':      safe_name,
        'extracted_fields':  ai.get('extracted_fields') or {},
        'confidence_scores': ai.get('confidence_scores') or {},
        'ai_used':           ai.get('ai_used', False),
        'ai_error':          ai.get('error'),
    })


# ═══════════════════════════════════════════════════════════════════
# FINANCIAL STATEMENTS API (PR-6)
# Derived views on top of customer_invoices + invoices + employees.
# All read-only; FX-normalized to TND at query time.
# ═══════════════════════════════════════════════════════════════════

def _stmt_year_month():
    """Parse year + optional month from query string, with safe defaults."""
    try:
        year = int(request.args.get('year') or datetime.utcnow().year)
    except (TypeError, ValueError):
        year = datetime.utcnow().year
    month_arg = request.args.get('month')
    try:
        month = int(month_arg) if month_arg else None
        if month is not None and not (1 <= month <= 12):
            month = None
    except (TypeError, ValueError):
        month = None
    return year, month


@app.route('/api/finance/statements/pnl', methods=['GET'])
@login_required
def api_statements_pnl():
    year, month = _stmt_year_month()
    basis = (request.args.get('basis') or 'accrual').lower()
    if basis not in ('accrual', 'cash'):
        basis = 'accrual'
    return jsonify(statements_engine.compute_pnl(fx_rates, year, month, basis=basis))


@app.route('/api/finance/statements/cashflow', methods=['GET'])
@login_required
def api_statements_cashflow():
    year, _m = _stmt_year_month()
    return jsonify(statements_engine.compute_cashflow(fx_rates, year))


@app.route('/api/finance/statements/revenue_breakdown', methods=['GET'])
@login_required
def api_statements_revenue_breakdown():
    year, _m = _stmt_year_month()
    return jsonify(statements_engine.compute_revenue_breakdown(fx_rates, year))


@app.route('/api/finance/statements/expense_breakdown', methods=['GET'])
@login_required
def api_statements_expense_breakdown():
    year, _m = _stmt_year_month()
    return jsonify(statements_engine.compute_expense_breakdown(fx_rates, year))


@app.route('/api/finance/statements/margins', methods=['GET'])
@login_required
def api_statements_margins():
    year, month = _stmt_year_month()
    return jsonify(statements_engine.compute_margins(fx_rates, year, month))


@app.route('/api/finance/statements/variance', methods=['GET'])
@login_required
def api_statements_variance():
    year, _m = _stmt_year_month()
    return jsonify(statements_engine.compute_planned_variance(fx_rates, year))


# ─── Forecast & risk ─────────────────────────────────────────────

@app.route('/api/finance/forecast', methods=['GET'])
@login_required
def api_finance_forecast():
    try:
        months = int(request.args.get('months') or 6)
        if months < 1: months = 1
        if months > 12: months = 12
    except (TypeError, ValueError):
        months = 6
    method = (request.args.get('method') or '').strip().lower() or None
    return jsonify({
        'revenue':  forecast_engine.compute_revenue_forecast(fx_rates, months, method),
        'expense':  forecast_engine.compute_expense_forecast(fx_rates, months, method),
        'cashflow': forecast_engine.compute_cashflow_forecast(fx_rates, months),
    })


@app.route('/api/finance/risk', methods=['GET'])
@login_required
def api_finance_risk():
    return jsonify({'alerts': forecast_engine.compute_risk_alerts(fx_rates)})


# ─── Finance AI assistant ────────────────────────────────────────

@app.route('/api/finance/assistant/suggestions', methods=['GET'])
@login_required
def api_finance_assistant_suggestions():
    return jsonify({'questions': finance_assistant.SUGGESTED_QUESTIONS})


@app.route('/api/finance/assistant/ask', methods=['POST'])
@login_required
@rate_limit(tag='fin_assistant', max_requests=30, window_seconds=60, by='user')
@audit(action='finance.assistant.ask', entity='finance_question')
def api_finance_assistant_ask():
    data = request.get_json(force=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'Question vide'}), 400
    if len(question) > 600:
        question = question[:600]
    history = data.get('history') or []
    # Normalize history to {role, text}
    norm_hist = []
    for m in history[-6:]:
        role = m.get('role') or 'user'
        if role not in ('user', 'model', 'assistant'):
            role = 'user'
        norm_hist.append({
            'role': 'model' if role in ('model', 'assistant') else 'user',
            'text': (m.get('text') or m.get('content') or '')[:1500],
        })
    result = finance_assistant.ask(fx_rates, question, history=norm_hist)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════
# PLANNED EXPENSES API (PR-5)
# Commitments not yet captured as vendor invoices (rent, software subs,
# office relocation, taxes due, etc.). Used by statements_engine to
# build forward-looking P&L lines and variance reports.
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/finance/planned', methods=['GET'])
@login_required
def api_planned_list():
    filters = {
        'status':   request.args.get('status')   or None,
        'category': request.args.get('category') or None,
        'year':     request.args.get('year')     or None,
    }
    return jsonify({'expenses': planned_store.list({k: v for k, v in filters.items() if v})})


@app.route('/api/finance/planned', methods=['POST'])
@login_required
@admin_required
@audit(action='planned.create', entity='planned_expense')
def api_planned_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'status': 'ok', 'expense': planned_store.add(data, created_by=_current_user_id())})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/finance/planned/update', methods=['POST'])
@login_required
@admin_required
@audit(action='planned.update', entity='planned_expense', entity_arg='id')
def api_planned_update():
    data = request.get_json(force=True) or {}
    eid = (data.pop('id', '') or '').strip()
    try:
        result = planned_store.update(eid, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Depense prevue introuvable'}), 404
    return jsonify({'status': 'ok', 'expense': result})


@app.route('/api/finance/planned/delete', methods=['POST'])
@login_required
@admin_required
@audit(action='planned.delete', entity='planned_expense', entity_arg='id')
def api_planned_delete():
    data = request.get_json(force=True) or {}
    if not planned_store.delete((data.get('id') or '').strip()):
        return jsonify({'error': 'Depense prevue introuvable'}), 404
    return jsonify({'status': 'ok'})


@app.route('/api/finance/planned/materialize', methods=['POST'])
@login_required
@admin_required
@audit(action='planned.materialize', entity='planned_expense', entity_arg='id')
def api_planned_materialize():
    data = request.get_json(force=True) or {}
    try:
        result = planned_store.materialize(
            (data.get('id') or '').strip(),
            (data.get('invoice_id') or '').strip(),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'Depense prevue introuvable'}), 404
    return jsonify({'status': 'ok', 'expense': result})


# ─── Finance rates (CNSS + corporate tax) ───────────────────────────

@app.route('/api/finance/rates', methods=['GET'])
@login_required
def api_finance_rates():
    return jsonify({'rates': get_finance_rates()})


@app.route('/api/finance/rates', methods=['POST'])
@login_required
@admin_required
@audit(action='finance.rates.update', entity='finance_rates')
def api_finance_rates_update():
    data = request.get_json(force=True) or {}
    return jsonify({'status': 'ok', 'rates': set_finance_rates(data)})


# ═══════════════════════════════════════════════════════════════════
# TRIPS & VISA MANAGEMENT API (all protected)
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/trips/overview')
@login_required
def api_trips_overview():
    return jsonify({
        'persons': trips_store.list_persons(),
        'dossiers': trips_store.list_dossiers(),
        'stats': trips_store.get_overview(),
        'templates': {'trip': TRIP_DOCS, 'visa': VISA_DOCS},
    })


@app.route('/api/trips/page')
@login_required
def trips_page():
    return jsonify({
        'dossiers': trips_store.list_dossiers(),
        'persons':  trips_store.list_persons(),
    })


@app.route('/api/trips/person', methods=['POST'])
@login_required
def api_trips_add_person():
    data = request.get_json(force=True)
    if not (data.get('first_name') or data.get('last_name')):
        return jsonify({'error': 'Nom requis'}), 400
    return jsonify({'status': 'ok', 'person': trips_store.add_person(data)})


@app.route('/api/trips/person/update', methods=['POST'])
@login_required
def api_trips_update_person():
    data = request.get_json(force=True)
    pid = data.pop('id', '')
    result = trips_store.update_person(pid, data)
    if result is None:
        return jsonify({'error': 'Personne non trouvee'}), 404
    return jsonify({'status': 'ok', 'person': result})


@app.route('/api/trips/person/delete', methods=['POST'])
@login_required
@audit(action='trips.person.delete', entity='person', entity_arg='id')
def api_trips_delete_person():
    data = request.get_json(force=True)
    if trips_store.delete_person(data.get('id', '')):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Personne non trouvee'}), 404


@app.route('/api/trips/dossier', methods=['POST'])
@login_required
@audit(action='trips.dossier.create', entity='dossier')
def api_trips_add_dossier():
    data = request.get_json(force=True)
    try:
        # New simplified flow: person info embedded in dossier creation
        if data.get('person_name') and not data.get('person_id'):
            dossier = trips_store.create_dossier_with_person(data)
        elif data.get('person_id'):
            dossier = trips_store.add_dossier(data)
        else:
            return jsonify({'error': 'Nom de la personne ou person_id requis'}), 400
        return jsonify({'status': 'ok', 'dossier': dossier})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/trips/dossier/update', methods=['POST'])
@login_required
def api_trips_update_dossier():
    data = request.get_json(force=True)
    did = data.pop('id', '')
    result = trips_store.update_dossier(did, data)
    if result is None:
        return jsonify({'error': 'Dossier non trouve'}), 404
    return jsonify({'status': 'ok', 'dossier': result})


@app.route('/api/trips/dossier/delete', methods=['POST'])
@login_required
@audit(action='trips.dossier.delete', entity='dossier', entity_arg='id')
def api_trips_delete_dossier():
    data = request.get_json(force=True)
    if trips_store.delete_dossier(data.get('id', '')):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Dossier non trouve'}), 404


@app.route('/api/trips/dossier/duplicate', methods=['POST'])
@login_required
def api_trips_duplicate_dossier():
    data = request.get_json(force=True)
    dup = trips_store.duplicate_dossier(
        data.get('id', ''),
        new_person_id=data.get('person_id') or None,
    )
    if dup is None:
        return jsonify({'error': 'Dossier non trouve'}), 404
    return jsonify({'status': 'ok', 'dossier': dup})


@app.route('/api/trips/document', methods=['POST'])
@login_required
def api_trips_update_document():
    data = request.get_json(force=True)
    did = data.pop('dossier_id', '')
    key = data.pop('doc_key', '')
    result = trips_store.update_document(did, key, data)
    if result is None:
        return jsonify({'error': 'Document non trouve'}), 404
    return jsonify({'status': 'ok', 'document': result})


@app.route('/api/trips/document/upload', methods=['POST'])
@login_required
def api_trips_upload_document():
    did = request.form.get('dossier_id', '')
    key = request.form.get('doc_key', '')
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Aucun fichier fourni'}), 400
    safe_name = secure_filename(f.filename) or 'document'
    blob = f.read()
    result = trips_store.update_document(did, key, {
        'file_name': safe_name,
        'file_size': len(blob),
        'status': 'completed',
    })
    if result is None:
        return jsonify({'error': 'Document non trouve'}), 404
    return jsonify({'status': 'ok', 'document': result})


# ═══════════════════════════════════════════════════════════════════
# OUTLOOK AI AGENT — Email Invoice Scanner
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/outlook/status')
@login_required
def api_outlook_status():
    return jsonify({
        'configured': outlook_agent.is_configured(),
        **outlook_agent.get_connection_status(),
    })


@app.route('/api/outlook/connect')
@login_required
def api_outlook_connect():
    """Start OAuth2 flow — redirect user to Microsoft login."""
    if not outlook_agent.is_configured():
        return jsonify({'error': 'OUTLOOK_CLIENT_ID / OUTLOOK_CLIENT_SECRET non configurés.'}), 400
    redirect_uri = request.host_url.rstrip('/') + '/api/outlook/callback'
    state = session.get('user_id', '')[:16]
    url = outlook_agent.get_auth_url(redirect_uri, state=state)
    return redirect(url)


@app.route('/api/outlook/callback')
def api_outlook_callback():
    """OAuth2 callback from Microsoft."""
    code  = request.args.get('code', '')
    error = request.args.get('error', '')
    if error:
        logger.warning('outlook_oauth_error: %s — %s', error, request.args.get('error_description', ''))
        return redirect('/?outlook_error=' + quote(error))
    if not code:
        return redirect('/?outlook_error=no_code')
    try:
        redirect_uri = request.host_url.rstrip('/') + '/api/outlook/callback'
        tokens = outlook_agent.exchange_code(code, redirect_uri)
        # Extract user email from id_token if available
        import base64 as _b64
        email = ''
        id_token = tokens.get('id_token', '')
        if id_token:
            try:
                payload = id_token.split('.')[1]
                payload += '=' * (4 - len(payload) % 4)
                claims = json.loads(_b64.b64decode(payload))
                email = claims.get('preferred_username', '') or claims.get('email', '')
            except Exception:
                pass
        outlook_agent.save_tokens(tokens, user_email=email)
        logger.info('outlook_connected email=%s', email)
        return redirect('/?outlook_connected=1')
    except Exception:
        logger.exception('outlook_oauth_exchange_failed')
        return redirect('/?outlook_error=token_exchange_failed')


@app.route('/api/outlook/disconnect', methods=['POST'])
@login_required
@audit(action='outlook.disconnect', entity='outlook')
def api_outlook_disconnect():
    outlook_agent.clear_tokens()
    return jsonify({'status': 'ok'})


@app.route('/api/outlook/scan', methods=['POST'])
@login_required
@rate_limit(tag='outlook_scan', max_requests=10, window_seconds=600, by='user')
@audit(action='outlook.scan', entity='email')
def api_outlook_scan():
    """Scan recent emails for invoices using keyword + Gemini AI."""
    data = request.get_json(silent=True) or {}
    hours = min(int(data.get('hours', 48)), 168)  # max 7 days
    try:
        results = outlook_agent.scan_emails(hours=hours)
        return jsonify({'status': 'ok', 'invoices': results, 'scanned_hours': hours})
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception('outlook_scan_failed')
        return jsonify({'error': 'Erreur lors du scan des emails.'}), 500


@app.route('/api/outlook/import', methods=['POST'])
@login_required
@rate_limit(tag='outlook_import', max_requests=20, window_seconds=600, by='user')
@audit(action='outlook.import', entity='invoice', entity_arg='email_id')
def api_outlook_import():
    """Import an invoice from an email attachment."""
    data = request.get_json(force=True)
    email_id = data.get('email_id', '')
    if not email_id:
        return jsonify({'error': 'email_id requis'}), 400
    try:
        result = outlook_agent.import_email_invoice(email_id)
        # Auto-create invoice in DB if extraction succeeded
        if result.get('ai_used') and result.get('extracted_fields'):
            fields = result['extracted_fields']
            fields['original_filename'] = result.get('original_filename', '')
            fields['notes'] = f"Importé depuis Outlook (email)"
            inv = inv_store.add_invoice(fields)
            result['invoice'] = inv
            result['auto_created'] = True
        return jsonify(result)
    except (RuntimeError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception('outlook_import_failed')
        return jsonify({'error': "Erreur lors de l'import."}), 500


# ═══════════════════════════════════════════════════════════════════
# MR HAMZA — AI HR LEGAL CHATBOT (RAG + Gemini)
# ═══════════════════════════════════════════════════════════════════

_MR_HAMZA_BASE = """Tu es Mr Hamza, assistant IA expert en ressources humaines et droit du travail tunisien, francais et international.

IDENTITE :
- Nom : Mr Hamza
- Role : Conseiller RH et juridique structure
- Langue : reponds TOUJOURS dans la langue de la question (FR par defaut / EN / AR)
- Ton : professionnel, precis, methodique

PRINCIPES :
- Tu cites les textes verbatim quand ils existent dans le contexte.
- Tu n'inventes JAMAIS un numero d'article, une loi ou une clause.
- Tu admets honnetement quand une information manque ("Cette donnee n'est pas dans la base de connaissances actuelle.")
"""

_FORMAT_BLOCK = """
STRUCTURE DE REPONSE OBLIGATOIRE (4 sections dans cet ordre exact) :

═══════════════════════════════════════════════
1. SOURCES & ARTICLES APPLICABLES
═══════════════════════════════════════════════
Pour chaque texte pertinent trouve dans le contexte :
- Citation VERBATIM entre guillemets francais (« »)
- Reference precise : numero d'article si present, section, chapitre
- Source : nom du fichier exactement comme il apparait dans le contexte
- Numero du passage tel qu'indique dans le contexte

Format type :
> « [citation exacte du texte] »
> — Article XX, [section / chapitre], **[nom_fichier.pdf]**, passage #N

S'il n'y a aucun article applicable dans le contexte, ecrire explicitement :
« Aucun article specifique trouve dans la base sur ce point. »

═══════════════════════════════════════════════
2. ANALYSE
═══════════════════════════════════════════════
- Interpretation des textes cites face a la question.
- Conditions, exceptions, articulation entre plusieurs textes.
- Si plusieurs sources se contredisent : les exposer et trancher avec justification.
- Si la question depasse le contexte fourni : signaler clairement quelle partie repose sur des connaissances generales.

═══════════════════════════════════════════════
3. REPONSE
═══════════════════════════════════════════════
Conclusion synthetique en 1-3 phrases qui repond directement a la question posee.
Si une action concrete est recommandee, la formuler en une ligne en fin de section.

═══════════════════════════════════════════════
4. AVERTISSEMENT
═══════════════════════════════════════════════
_Ces informations sont fournies a titre indicatif et ne constituent pas un conseil juridique definitif. Verifiez avec un professionnel pour toute decision a enjeu._
"""

_MR_HAMZA_WITH_DOCS = _MR_HAMZA_BASE + _FORMAT_BLOCK + """

MODE : DOCUMENTS DISPONIBLES
Des extraits pertinents ont ete trouves dans la base de connaissances.

REGLES DE CITATION DANS LA SECTION 1 :
- Cite UNIQUEMENT ce qui est present dans les extraits fournis.
- Ne fabrique aucun numero d'article, aucune date de loi, aucun nom de signataire.
- Si plusieurs passages traitent le meme sujet, les regrouper par theme et tous les citer.
- L'ordre des citations dans la section 1 doit refleter leur pertinence par rapport a la question, pas leur ordre dans le contexte.

REGLES POUR LA SECTION 2 (analyse) :
- Tu PEUX compléter avec des connaissances generales RH ou juridiques, mais signale chaque ajout par
  une mention type "(connaissance generale, non issue des documents)".
- Compare les sources entre elles si elles disent des choses differentes.

REGLES POUR LA SECTION 3 :
- La conclusion doit etre directement deductible de la section 1. Si tu ne peux pas, dis-le.
"""

_MR_HAMZA_NO_DOCS = _MR_HAMZA_BASE + _FORMAT_BLOCK + """

MODE : CONNAISSANCES GENERALES (aucun extrait pertinent trouve)

DANS LA SECTION 1 :
- Ecrire explicitement : « Aucun extrait specifique trouve dans la base de connaissances pour cette question. »
- Lister cependant les sources de reference connues : "Code du Travail tunisien (Loi 66-27 du 30 avril 1966 et amendements ulterieurs)", "Convention collective sectorielle applicable", etc.
- Marquer chaque element par "(connaissance generale)".

DANS LA SECTION 2 :
- Analyse complete fondee sur tes connaissances generales en droit du travail tunisien et compare.
- Reste prudent : enonce les principes generaux, pas de chiffres ou dates non verifiables.

DANS LA SECTION 3 :
- Conclusion + recommandation explicite de verifier dans la version officielle du texte applicable.
"""

# Minimum cosine similarity to consider a chunk "relevant"
# Cosine similarity below this is considered a non-match. Lowered from 0.22
# to 0.18 to surface more lateral hits (the model is now strict enough about
# only citing what's actually in context that we can afford the wider net).
_RAG_THRESHOLD = 0.18


@app.route('/api/chat/status')
@login_required
def api_chat_status():
    stats = rag_store.stats()
    return jsonify({
        'available': gemini_configured(),
        'stats': stats,
        'categories': RAG_CATEGORIES,
        'is_admin': session.get('user_role') == 'admin',
    })


@app.route('/api/chat/documents')
@login_required
def api_chat_documents():
    return jsonify({
        'documents': rag_store.list_documents(),
        'categories': RAG_CATEGORIES,
    })


@app.route('/api/chat/upload', methods=['POST'])
@login_required
@rate_limit(tag='chat_upload', max_requests=10, window_seconds=3600, by='user')
@audit(action='rag.upload', entity='rag_document')
def api_chat_upload():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Action reservee aux administrateurs'}), 403
    f = request.files.get('file')
    category = request.form.get('category', 'other')
    if not f or not f.filename:
        return jsonify({'error': 'Aucun fichier fourni'}), 400
    safe_name = secure_filename(f.filename) or 'document'
    allowed = ('.pdf', '.docx', '.txt', '.md')
    if not safe_name.lower().endswith(allowed):
        return jsonify({'error': f"Format non supporte. Utilisez : {', '.join(allowed)}"}), 400
    try:
        blob = f.read()
        if len(blob) > 50 * 1024 * 1024:
            return jsonify({'error': 'Fichier trop volumineux (max 50 Mo)'}), 400
        result = rag_store.add_document(safe_name, blob, category=category)
        return jsonify({'status': 'ok', **result})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception('chat_upload_failed')
        return jsonify({'error': "Echec de l'indexation du document. Verifiez le format et reessayez."}), 500


@app.route('/api/chat/delete', methods=['POST'])
@login_required
@audit(action='rag.delete', entity='rag_document', entity_arg='id')
def api_chat_delete():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Action reservee aux administrateurs'}), 403
    data = request.get_json(force=True)
    doc_id = (data.get('id') or '').strip()
    if doc_id.startswith('builtin_'):
        return jsonify({'error': 'Cette reference est integree et ne peut pas etre supprimee.'}), 400
    if rag_store.delete_document(doc_id):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Document non trouve'}), 404


@app.route('/api/chat/query', methods=['POST'])
@login_required
@rate_limit(tag='chat_query', max_requests=30, window_seconds=60, by='user')
def api_chat_query():
    data = request.get_json(force=True)
    question = (data.get('message') or '').strip()
    if not question:
        return jsonify({'error': 'Question vide'}), 400
    if len(question) > 2000:
        question = question[:2000]

    # ── Conversation memory ───────────────────────────────────────────
    raw_history = data.get('history', []) or []
    history = []
    for m in raw_history[-10:]:
        role = m.get('role', '')
        if role in ('user', 'assistant', 'model'):
            history.append({
                'role': 'model' if role in ('assistant', 'model') else 'user',
                'text': (m.get('text') or m.get('content') or '')[:3000],
            })

    # ── RAG retrieval ─────────────────────────────────────────────────
    # We over-retrieve (top_k=12) then keep the strongest signal first.
    # Threshold lowered slightly (0.18) to catch lateral hits that the
    # model can still discard in the analysis section.
    all_results  = rag_store.search(question, top_k=12, threshold=0.0)
    good_results = [r for r in all_results if r['score'] >= _RAG_THRESHOLD]

    has_kb_docs  = rag_store.has_documents()
    if good_results:
        # Deduplicate near-identical chunks (same doc + adjacent indexes)
        # to give the model diverse passages, then keep the top 8.
        seen_keys = set()
        deduped = []
        for r in good_results:
            # Two chunks <= 1 apart in the same doc are treated as one neighborhood
            key = (r['doc_id'], r['chunk_idx'] // 2)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(r)
        results      = deduped[:8]
        system_prompt = _MR_HAMZA_WITH_DOCS
        rag_mode     = 'documents'
    elif has_kb_docs and all_results:
        # KB has documents but none closely matched — pass top-4 anyway
        results      = all_results[:4]
        system_prompt = _MR_HAMZA_WITH_DOCS
        rag_mode     = 'documents_weak'
    else:
        results      = []
        system_prompt = _MR_HAMZA_NO_DOCS
        rag_mode     = 'general'

    # ── Build prompt ──────────────────────────────────────────────────
    if results:
        # Citation-friendly block format. The model is told in the system
        # prompt to quote VERBATIM with the source + passage number.
        context_blocks = []
        for r in results:
            cat_label = r.get('category_label') or 'Document'
            builtin_marker = ' • Reference interne' if r.get('is_builtin') else ''
            score_pct = int(round(r['score'] * 100))
            context_blocks.append(
                "┌─────────────────────────────────────────\n"
                f"│ SOURCE   : {r['filename']}{builtin_marker}\n"
                f"│ TYPE     : {cat_label}\n"
                f"│ PASSAGE  : #{r['chunk_idx'] + 1}\n"
                f"│ PERTINENCE : {score_pct}%\n"
                "└─────────────────────────────────────────\n"
                f"{r['text']}"
            )
        context = '\n\n'.join(context_blocks)
        user_prompt = (
            "CONTEXTE — extraits authentiques de la base de connaissances "
            "(a citer VERBATIM dans la section 1 de ta reponse) :\n\n"
            f"{context}\n\n"
            f"{'═' * 60}\n\n"
            f"QUESTION : {question}\n\n"
            "Reponds en respectant STRICTEMENT la structure en 4 sections "
            "(Sources & Articles → Analyse → Reponse → Avertissement)."
        )
    else:
        user_prompt = f"QUESTION : {question}"

    # ── Generate ──────────────────────────────────────────────────────
    try:
        answer = gemini_generate(
            system_prompt,
            user_prompt,
            history=history,
            temperature=0.3,
            max_tokens=2000,
        )
    except Exception:
        logger.exception('gemini_chat_failed')
        return jsonify({'error': 'Le service IA est momentanement indisponible. Reessayez dans quelques instants.'}), 503

    # ── Sources (deduped by filename, only good matches) ──────────────
    seen    = set()
    sources = []
    for r in (good_results or all_results[:3]):
        if r['filename'] in seen:
            continue
        seen.add(r['filename'])
        sources.append({
            'filename': r['filename'],
            'category': r['category_label'],
            'score':    round(float(r['score']), 3),
            'preview':  r['text'][:220] + ('...' if len(r['text']) > 220 else ''),
        })
        if len(sources) >= 4:
            break

    return jsonify({
        'answer':           answer,
        'sources':          sources,
        'retrieved_chunks': len(results),
        'rag_mode':         rag_mode,
    })


# ---------------------------------------------------------------------------
# API versioning — register /api/v1/* aliases for every /api/* route.
# Existing /api/* paths keep working (backward-compatible); new clients can
# start calling /api/v1/* immediately. Future /api/v2/* can then diverge.
# ---------------------------------------------------------------------------
_versioned_routes = []
for _rule in list(app.url_map.iter_rules()):
    _path = str(_rule.rule)
    if _path.startswith('/api/') and not _path.startswith('/api/v1/'):
        _v1_path = '/api/v1' + _path[len('/api'):]
        _view    = app.view_functions.get(_rule.endpoint)
        if _view is None:
            continue
        app.add_url_rule(
            _v1_path,
            endpoint=f"{_rule.endpoint}__v1",
            view_func=_view,
            methods=list(_rule.methods - {'HEAD', 'OPTIONS'}),
        )
        _versioned_routes.append(_v1_path)

logger.info('api_versioning registered %d /api/v1 aliases', len(_versioned_routes))


# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
