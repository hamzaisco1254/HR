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
from trips_store import TripsStore, TRIP_DOCS, VISA_DOCS
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
    data = request.get_json(force=True)
    try:
        user = user_store.add_user(
            email=data.get('email', ''),
            password=data.get('password', ''),
            name=data.get('name', ''),
        )
        return jsonify({'status': 'ok', 'user': user})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


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
    data = request.get_json(force=True)
    target_id = data.get('id', session.get('user_id'))
    # Non-admin can only change own password
    if session.get('user_role') != 'admin' and target_id != session.get('user_id'):
        return jsonify({'error': 'Non autorisé'}), 403
    try:
        if user_store.change_password(target_id, data.get('password', '')):
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


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
            year = int(request.form.get('ref_year', ref_mgr.get_year_prefix()))
            counter = int(request.form.get('ref_counter', ref_mgr.get_counter()))
            ref_mgr.set_year_prefix(year)
            ref_mgr.set_counter(counter)
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

_MR_HAMZA_BASE = """Tu es Mr Hamza, un assistant IA expert en ressources humaines, droit du travail (tunisien, français et international), gestion d'entreprise et conseil juridique RH.

IDENTITE :
- Nom : Mr Hamza
- Rôle : Conseiller RH & juridique intelligent
- Langues : Réponds TOUJOURS dans la langue de la question — français (défaut), anglais, arabe
- Ton : Professionnel, précis, structuré, utile

RÈGLES GÉNÉRALES :
1. Tu réponds TOUJOURS. Ne dis jamais que tu ne peux pas répondre.
2. Adapte automatiquement la langue à celle de la question (FR / EN / AR).
3. Structure tes réponses : réponse directe → développement → sources → disclaimer.
4. Ne JAMAIS inventer d'articles, de numéros de loi ou de clauses inexistants.
5. Termine chaque réponse par : _Ces informations sont fournies à titre indicatif et ne constituent pas un conseil juridique définitif._"""

_MR_HAMZA_WITH_DOCS = _MR_HAMZA_BASE + """

MODE : DOCUMENTS DISPONIBLES
Des extraits pertinents ont été trouvés dans la base de connaissances interne.
- Priorise ces extraits pour répondre et cite-les précisément (nom du fichier + numéro de passage).
- Complète avec tes connaissances générales RH/juridiques si nécessaire, en le signalant.
- Si plusieurs sources traitent le même sujet, compare-les et signale les contradictions éventuelles.
- Mentionne les articles, sections et chapitres présents dans les extraits."""

_MR_HAMZA_NO_DOCS = _MR_HAMZA_BASE + """

MODE : CONNAISSANCES GÉNÉRALES
Aucun extrait spécifiquement pertinent n'a été trouvé dans la base de connaissances sur ce sujet.
- Réponds en te basant sur tes connaissances en droit du travail, RH et gestion d'entreprise.
- Pour les questions sur la Tunisie, réfère-toi au Code du Travail tunisien (Loi 66-27 et modifications).
- Indique clairement que ta réponse est basée sur tes connaissances générales (pas un document interne).
- Recommande de vérifier avec les documents internes de l'entreprise ou un juriste si nécessaire."""

# Minimum cosine similarity to consider a chunk "relevant"
_RAG_THRESHOLD = 0.22


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
    if rag_store.delete_document(data.get('id', '')):
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

    # ── RAG retrieval with relevance threshold ────────────────────────
    all_results  = rag_store.search(question, top_k=8, threshold=0.0)
    good_results = [r for r in all_results if r['score'] >= _RAG_THRESHOLD]

    # Use good results if any; fall back to top-3 raw if KB has docs
    # but nothing scored high (still gives LLM something to work with)
    has_kb_docs  = rag_store.has_documents()
    if good_results:
        results      = good_results[:6]
        system_prompt = _MR_HAMZA_WITH_DOCS
        rag_mode     = 'documents'
    elif has_kb_docs and all_results:
        # KB has documents but none closely matched — pass top-3 anyway
        results      = all_results[:3]
        system_prompt = _MR_HAMZA_WITH_DOCS
        rag_mode     = 'documents_weak'
    else:
        results      = []
        system_prompt = _MR_HAMZA_NO_DOCS
        rag_mode     = 'general'

    # ── Build prompt ──────────────────────────────────────────────────
    if results:
        context_blocks = []
        for r in results:
            context_blocks.append(
                f"[SOURCE : {r['filename']} | {r['category_label']} | Passage #{r['chunk_idx'] + 1} | Score : {r['score']:.2f}]\n{r['text']}"
            )
        context = '\n\n────────\n\n'.join(context_blocks)
        user_prompt = (
            f"CONTEXTE — extraits des documents internes :\n\n{context}\n\n"
            f"{'═' * 40}\n\n"
            f"QUESTION : {question}"
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
