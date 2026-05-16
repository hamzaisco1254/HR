"""
Email service — stdlib SMTP wrapper for transactional emails.

Used today only for sending account credentials. Designed so any
SMTP provider works: Gmail, Outlook 365, Mailgun, SendGrid, Resend,
AWS SES (with SMTP creds), or a self-hosted relay.

REQUIRED environment variables (server-side, set in Vercel):
    SMTP_HOST       smtp.gmail.com / smtp.office365.com / smtp.mailgun.org / ...
    SMTP_PORT       587 (TLS, default) or 465 (SSL) or 25
    SMTP_USERNAME   The auth username (often the from-address itself)
    SMTP_PASSWORD   App password / API key
    SMTP_FROM       "Planisware HR <noreply@example.com>"
    APP_URL         https://hr-yourdomain.vercel.app  (used in email links)

OPTIONAL:
    SMTP_USE_SSL    "true" to use SSL on port 465 instead of STARTTLS

Behavior:
- If SMTP_HOST is not set, send_*() returns {'sent': False, 'reason':
  'smtp_not_configured'} and the caller is expected to fall back to
  surfacing the password to the admin in the UI.
- Failures don't raise — they return a dict so the caller can decide
  whether to roll back or keep going.
"""
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Dict, Optional

logger = logging.getLogger('plw.email')


def _cfg() -> Dict[str, str]:
    return {
        'host':     (os.environ.get('SMTP_HOST') or '').strip(),
        'port':     int(os.environ.get('SMTP_PORT') or '587'),
        'username': (os.environ.get('SMTP_USERNAME') or '').strip(),
        'password':  os.environ.get('SMTP_PASSWORD') or '',
        'from':     (os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USERNAME') or '').strip(),
        'use_ssl':  (os.environ.get('SMTP_USE_SSL') or '').strip().lower() in ('1', 'true', 'yes'),
        'app_url':  (os.environ.get('APP_URL') or '').strip().rstrip('/'),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c['host'] and c['from'])


def status() -> Dict:
    """Return a redacted config snapshot for the admin UI."""
    c = _cfg()
    return {
        'configured':  is_configured(),
        'host':        c['host'],
        'port':        c['port'],
        'from':        c['from'],
        'app_url':     c['app_url'],
        'has_credentials': bool(c['username'] and c['password']),
        'use_ssl':     c['use_ssl'],
    }


def _send_raw(to_email: str, subject: str, text_body: str, html_body: str) -> Dict:
    """Internal: actually open an SMTP connection and ship the message."""
    c = _cfg()
    if not c['host'] or not c['from']:
        return {'sent': False, 'reason': 'smtp_not_configured'}

    msg = MIMEMultipart('alternative')
    msg['Subject']  = subject
    msg['From']     = c['from']
    msg['To']       = to_email
    msg['Date']     = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()

    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        if c['use_ssl'] or c['port'] == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(c['host'], c['port'], context=context, timeout=20) as s:
                if c['username']:
                    s.login(c['username'], c['password'])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c['host'], c['port'], timeout=20) as s:
                s.ehlo()
                try:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                except smtplib.SMTPException:
                    # Server didn't support STARTTLS — best-effort plain (e.g. local relay)
                    logger.warning('STARTTLS not supported by %s — sending unencrypted', c['host'])
                if c['username']:
                    s.login(c['username'], c['password'])
                s.send_message(msg)
        logger.info('email_sent to=%s subject=%r', to_email, subject)
        return {'sent': True}
    except smtplib.SMTPAuthenticationError as e:
        logger.exception('SMTP auth failed')
        return {'sent': False, 'reason': 'auth_failed', 'detail': str(e)}
    except smtplib.SMTPRecipientsRefused as e:
        logger.exception('SMTP recipients refused')
        return {'sent': False, 'reason': 'recipient_refused', 'detail': str(e)}
    except Exception as e:
        logger.exception('SMTP send failed')
        return {'sent': False, 'reason': 'send_failed', 'detail': str(e)}


# ─── Templates ───────────────────────────────────────────────────────

def _credentials_template(name: str, email: str, password: str, role: str,
                          login_url: str, is_resend: bool) -> tuple:
    role_label = {'admin': 'Administrateur', 'user': 'Utilisateur'}.get(role, role)
    title = "Reinitialisation de votre acces" if is_resend else "Votre acces a la plateforme Planisware HR"

    text = f"""Bonjour {name or email},

{'Votre mot de passe a ete reinitialise.' if is_resend else 'Un compte vient de vous etre cree sur la plateforme Planisware HR.'}

Identifiants :
  Email      : {email}
  Mot de passe : {password}
  Role       : {role_label}

Connexion : {login_url or '(URL non configuree)'}

Important :
- Pour des raisons de securite, vous serez invite a definir un nouveau
  mot de passe lors de votre premiere connexion.
- Ne partagez ce mot de passe avec personne.
- Si vous n'avez pas demande cet acces, ignorez cet email.

L'equipe Planisware HR
"""

    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f3f4f6;padding:24px;color:#111827;">
<div style="max-width:560px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
  <div style="background:linear-gradient(135deg,#ec4899,#7c3aed);padding:24px;color:white;">
    <h1 style="margin:0;font-size:20px;font-weight:800;">Planisware HR</h1>
    <p style="margin:4px 0 0;font-size:13px;opacity:0.9;">{title}</p>
  </div>
  <div style="padding:24px;">
    <p style="font-size:14px;line-height:1.6;">Bonjour <strong>{name or email}</strong>,</p>
    <p style="font-size:13px;line-height:1.6;color:#374151;">
      {'Votre mot de passe a ete reinitialise. Voici vos nouveaux identifiants temporaires :' if is_resend else 'Un compte vient de vous etre cree sur la plateforme Planisware HR. Voici vos identifiants temporaires :'}
    </p>
    <table style="width:100%;background:#f9fafb;border-radius:8px;padding:16px;margin:18px 0;border:1px solid #e5e7eb;">
      <tr><td style="padding:6px 0;font-size:12px;color:#6b7280;">Email</td>
          <td style="padding:6px 0;font-size:13px;font-weight:700;text-align:right;font-family:'SFMono-Regular',Consolas,monospace;">{email}</td></tr>
      <tr><td style="padding:6px 0;font-size:12px;color:#6b7280;">Mot de passe</td>
          <td style="padding:6px 0;font-size:14px;font-weight:800;text-align:right;font-family:'SFMono-Regular',Consolas,monospace;color:#be185d;background:#fce7f3;padding-left:8px;padding-right:8px;border-radius:4px;">{password}</td></tr>
      <tr><td style="padding:6px 0;font-size:12px;color:#6b7280;">Role</td>
          <td style="padding:6px 0;font-size:13px;font-weight:700;text-align:right;">{role_label}</td></tr>
    </table>
    {'<p style="text-align:center;margin:24px 0;"><a href="' + login_url + '" style="display:inline-block;background:#ec4899;color:white;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:700;font-size:13px;">Se connecter</a></p>' if login_url else ''}
    <div style="background:#fef3c7;border-left:3px solid #f59e0b;padding:12px 14px;font-size:12px;color:#92400e;line-height:1.6;border-radius:0 6px 6px 0;margin-top:18px;">
      <strong>Important :</strong> pour des raisons de securite, vous serez invite a definir un nouveau mot de passe lors de votre premiere connexion. Ne partagez ce mot de passe avec personne.
    </div>
    <p style="font-size:11px;color:#9ca3af;margin-top:20px;line-height:1.5;">
      Si vous n'avez pas demande cet acces, vous pouvez ignorer cet email.
    </p>
  </div>
  <div style="background:#f9fafb;padding:12px 24px;border-top:1px solid #e5e7eb;font-size:10px;color:#9ca3af;text-align:center;">
    Planisware HR — Notification automatique
  </div>
</div>
</body></html>"""

    return text, html


def send_credentials(to_email: str, name: str, password: str, role: str = 'user',
                     is_resend: bool = False) -> Dict:
    """Send a credential email. Returns dict {sent: bool, reason?, detail?}."""
    cfg = _cfg()
    login_url = (cfg['app_url'] + '/login') if cfg['app_url'] else ''
    subject = ('[Planisware HR] Reinitialisation de votre mot de passe'
               if is_resend else
               '[Planisware HR] Votre acces a la plateforme')
    text, html = _credentials_template(name, to_email, password, role, login_url, is_resend)
    return _send_raw(to_email, subject, text, html)


def send_test(to_email: str) -> Dict:
    """Send a one-line 'connection works' test email (admin self-service)."""
    if not is_configured():
        return {'sent': False, 'reason': 'smtp_not_configured'}
    text = "Test SMTP Planisware HR. Si vous recevez ce message, la configuration fonctionne."
    html = ("<p style='font-family:sans-serif'>Test SMTP <strong>Planisware HR</strong>.</p>"
            "<p style='font-family:sans-serif;font-size:12px;color:#666'>Si vous recevez ce message, la configuration fonctionne.</p>")
    return _send_raw(to_email, '[Planisware HR] Test SMTP', text, html)
