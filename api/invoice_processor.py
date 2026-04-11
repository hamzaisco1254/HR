"""
AI-powered invoice processing using Google Gemini.

Extracts structured data from uploaded invoice files (PDF, PNG, JPG).
Returns extracted fields with per-field confidence scores.

Uses gemini_client.py (gemini-2.5-flash with vision) for extraction.
If Gemini is not configured, returns empty fields for manual entry.
"""
import base64
import json
import os
import re
import requests
from typing import Dict, Optional

from gemini_client import is_configured, generate, GEMINI_API_KEY, BASE_URL, CHAT_MODEL, CHAT_MODEL_FALLBACK

_EXTRACTION_PROMPT = """You are a financial document processing expert. Analyze this invoice image/document and extract the following fields as structured JSON. The invoice may be in French or English or Arabic.

Return ONLY a valid JSON object with these exact keys:

{
  "supplier_name": "Name of the supplier/vendor company",
  "invoice_date": "YYYY-MM-DD format",
  "due_date": "YYYY-MM-DD format or empty string if not found",
  "amount": 0.00,
  "currency": "TND or EUR or USD",
  "payment_status": "paid or unpaid or uncertain",
  "invoice_ref": "Invoice number/reference",
  "confidence": {
    "supplier_name": 0.95,
    "invoice_date": 0.90,
    "due_date": 0.80,
    "amount": 0.95,
    "currency": 0.90,
    "payment_status": 0.70,
    "invoice_ref": 0.85
  }
}

Rules:
- For amounts, extract the total amount (TTC/gross). Use a float number without currency symbol.
- For currency: detect from symbols (DT/TND for Tunisian Dinar, € for Euro, $ for USD). Default to TND if ambiguous and the document appears Tunisian.
- For payment_status: look for stamps/text like "PAYÉ", "PAID", "RÉGLÉ" → "paid". If unclear → "uncertain". Otherwise → "unpaid".
- For dates, convert to YYYY-MM-DD. If only month/year, use the 1st of the month.
- Confidence scores range from 0.0 to 1.0. Be honest about uncertainty — low quality scans or missing fields should get lower scores.
- If a field cannot be extracted at all, use an empty string for text fields, 0.0 for amount, and set confidence to 0.0.
- Do NOT wrap the JSON in markdown code blocks. Return raw JSON only."""


def _get_mime_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return {
        '.pdf':  'application/pdf',
        '.png':  'image/png',
        '.jpg':  'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.gif':  'image/gif',
    }.get(ext, 'application/octet-stream')


def _call_gemini_vision(file_bytes: bytes, mime: str, model: str) -> str:
    """
    Call Gemini generateContent with an inline file part + text prompt.
    Returns the raw text response.
    """
    b64 = base64.standard_b64encode(file_bytes).decode('ascii')
    url = f"{BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"

    body = {
        'contents': [{
            'role': 'user',
            'parts': [
                {
                    'inline_data': {
                        'mime_type': mime,
                        'data': b64,
                    }
                },
                {
                    'text': _EXTRACTION_PROMPT,
                },
            ],
        }],
        'generationConfig': {
            'temperature': 0.1,
            'maxOutputTokens': 1024,
            'topP': 0.95,
        },
        'safetySettings': [
            {'category': 'HARM_CATEGORY_HARASSMENT',        'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_HATE_SPEECH',       'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'},
        ],
    }

    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    try:
        parts = data['candidates'][0].get('content', {}).get('parts', [])
        return ''.join(p.get('text', '') for p in parts).strip()
    except (KeyError, IndexError):
        return ''


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from a string."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r'^```[a-z]*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    return text.strip()


def process_invoice(file_bytes: bytes, filename: str) -> Dict:
    """
    Process an invoice file and extract structured data using Gemini vision.

    Parameters
    ----------
    file_bytes : bytes
        Raw file content.
    filename : str
        Original filename (used for MIME detection).

    Returns
    -------
    dict with keys:
        extracted_fields : dict   — the structured invoice data
        confidence_scores : dict  — per-field confidence (0–1)
        ai_used : bool            — whether AI was actually used
        error : str | None        — error message if extraction failed
    """
    if not is_configured():
        return {
            'extracted_fields': _empty_fields(),
            'confidence_scores': _zero_confidence(),
            'ai_used': False,
            'error': 'Gemini API non configurée. Saisie manuelle requise.',
        }

    mime = _get_mime_type(filename)

    # Try primary model then fallback
    raw_text = ''
    last_error = None
    for model in (CHAT_MODEL, CHAT_MODEL_FALLBACK):
        try:
            raw_text = _call_gemini_vision(file_bytes, mime, model)
            if raw_text:
                break
        except Exception as e:
            last_error = e
            continue

    if not raw_text:
        return {
            'extracted_fields': _empty_fields(),
            'confidence_scores': _zero_confidence(),
            'ai_used': True,
            'error': f'Gemini n\'a pas retourné de réponse: {last_error}',
        }

    try:
        cleaned = _strip_fences(raw_text)
        parsed = json.loads(cleaned)

        confidence = parsed.pop('confidence', {})
        fields = {k: parsed.get(k, '') for k in (
            'supplier_name', 'invoice_date', 'due_date', 'amount',
            'currency', 'payment_status', 'invoice_ref',
        )}

        # Ensure amount is float
        try:
            fields['amount'] = float(fields['amount'])
        except (ValueError, TypeError):
            fields['amount'] = 0.0

        return {
            'extracted_fields': fields,
            'confidence_scores': confidence,
            'ai_used': True,
            'error': None,
        }

    except json.JSONDecodeError as e:
        return {
            'extracted_fields': _empty_fields(),
            'confidence_scores': _zero_confidence(),
            'ai_used': True,
            'error': f'Erreur de parsing JSON depuis Gemini: {e}',
        }
    except Exception as e:
        return {
            'extracted_fields': _empty_fields(),
            'confidence_scores': _zero_confidence(),
            'ai_used': True,
            'error': f'Erreur lors du traitement IA: {e}',
        }


def generate_ai_insights(invoices: list, balances: list, kpis: list) -> Optional[str]:
    """
    Generate a brief AI-powered financial summary using Gemini.

    Returns a short French paragraph or None if Gemini is not configured.
    """
    if not is_configured():
        return None

    summary_data = json.dumps({
        'invoice_count': len(invoices),
        'total_paid':   sum(i['amount'] for i in invoices if i.get('payment_status') == 'paid'),
        'total_unpaid': sum(i['amount'] for i in invoices if i.get('payment_status') in ('unpaid', 'overdue')),
        'balances': [
            {'name': b['name'], 'balance': b['balance'], 'currency': b['currency']}
            for b in balances
        ],
        'kpis': kpis,
    }, ensure_ascii=False)

    user_prompt = (
        "Tu es un analyste financier. Voici les données financières d'une entreprise tunisienne. "
        "Rédige un résumé concis (3-5 phrases) en français des points clés, risques et recommandations.\n\n"
        f"{summary_data}"
    )

    try:
        return generate(
            system_prompt="Tu es un analyste financier expert spécialisé dans les entreprises tunisiennes.",
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=500,
        ) or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_fields() -> Dict:
    return {
        'supplier_name':  '',
        'invoice_date':   '',
        'due_date':       '',
        'amount':         0.0,
        'currency':       'TND',
        'payment_status': 'unpaid',
        'invoice_ref':    '',
    }


def _zero_confidence() -> Dict:
    return {k: 0.0 for k in (
        'supplier_name', 'invoice_date', 'due_date', 'amount',
        'currency', 'payment_status', 'invoice_ref',
    )}
