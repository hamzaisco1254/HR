"""
AI-powered invoice processing using Anthropic's Claude.

Extracts structured data from uploaded invoice files (PDF, PNG, JPG).
Returns extracted fields with per-field confidence scores.

Set the ANTHROPIC_API_KEY environment variable to enable AI extraction.
If the key is not set, the processor returns empty fields for manual entry.
"""
import base64
import json
import os
from typing import Dict, Optional, Tuple

# The anthropic import is deferred so the module loads even if the
# package isn't installed (manual-only mode).

_EXTRACTION_PROMPT = """You are a financial document processing expert. Analyze this invoice and extract the following fields as structured JSON. The invoice may be in French or English.

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
        '.pdf': 'application/pdf',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
    }.get(ext, 'application/octet-stream')


def process_invoice(file_bytes: bytes, filename: str) -> Dict:
    """
    Process an invoice file and extract structured data.

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
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()

    if not api_key:
        return {
            'extracted_fields': _empty_fields(),
            'confidence_scores': _zero_confidence(),
            'ai_used': False,
            'error': 'ANTHROPIC_API_KEY non configurée. Saisie manuelle requise.',
        }

    try:
        import anthropic
    except ImportError:
        return {
            'extracted_fields': _empty_fields(),
            'confidence_scores': _zero_confidence(),
            'ai_used': False,
            'error': 'Module anthropic non installé.',
        }

    mime = _get_mime_type(filename)
    b64 = base64.standard_b64encode(file_bytes).decode('ascii')

    # Build the message content depending on file type
    if mime == 'application/pdf':
        file_content_block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": b64,
            },
        }
    else:
        file_content_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": b64,
            },
        }

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    file_content_block,
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                ],
            }],
        )

        raw_text = message.content[0].text.strip()
        # Strip markdown fences if present
        if raw_text.startswith('```'):
            raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
            if raw_text.endswith('```'):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

        parsed = json.loads(raw_text)

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
            'error': f'Erreur de parsing JSON depuis l\'IA: {e}',
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
    Generate a brief AI-powered financial summary.

    Returns a short French paragraph or None if the API key is not set.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    summary_data = json.dumps({
        'invoice_count': len(invoices),
        'total_paid': sum(i['amount'] for i in invoices if i.get('payment_status') == 'paid'),
        'total_unpaid': sum(i['amount'] for i in invoices if i.get('payment_status') in ('unpaid', 'overdue')),
        'balances': [{'name': b['name'], 'balance': b['balance'], 'currency': b['currency']} for b in balances],
        'kpis': kpis,
    }, ensure_ascii=False)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": (
                    "Tu es un analyste financier. Voici les données financières d'une entreprise tunisienne. "
                    "Rédige un résumé concis (3-5 phrases) en français des points clés, risques et recommandations.\n\n"
                    f"{summary_data}"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_fields() -> Dict:
    return {
        'supplier_name': '',
        'invoice_date': '',
        'due_date': '',
        'amount': 0.0,
        'currency': 'TND',
        'payment_status': 'unpaid',
        'invoice_ref': '',
    }


def _zero_confidence() -> Dict:
    return {k: 0.0 for k in (
        'supplier_name', 'invoice_date', 'due_date', 'amount',
        'currency', 'payment_status', 'invoice_ref',
    )}
