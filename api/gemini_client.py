"""
Thin wrapper around Google's Gemini REST API.

Handles embeddings (gemini-embedding-001) and chat completions
(gemini-2.5-flash) via plain HTTPS requests - no SDK dependency.

The API key falls back to the one provided by the project owner.
Override by setting the GEMINI_API_KEY environment variable.
"""
import os
import time
import requests

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()

EMBED_MODEL = 'gemini-embedding-001'
CHAT_MODEL = 'gemini-2.5-flash'
BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/models'

# Fallback chat model if primary is rate-limited/unavailable
CHAT_MODEL_FALLBACK = 'gemini-2.5-flash-lite'


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


# ─── Rate-limit config ───────────────────────────────────────────────
# gemini-embedding-001 free tier: ~1 500 RPM → ~25 RPS
# We target ~12 RPS (80 ms gap) to stay comfortably under the burst limit.
_EMBED_DELAY   = 0.08   # seconds between embedding requests
_RETRY_MAX     = 6      # maximum retry attempts on 429
_RETRY_BASE    = 2.0    # base wait in seconds (doubles each retry)


# ─── Embeddings ──────────────────────────────────────────────────────
def embed_text(text: str, task_type: str = 'RETRIEVAL_DOCUMENT') -> list:
    """
    Embed a single text string and return its vector.
    Retries up to _RETRY_MAX times with exponential backoff on 429.
    """
    url = f"{BASE_URL}/{EMBED_MODEL}:embedContent?key={GEMINI_API_KEY}"
    body = {
        'model': f'models/{EMBED_MODEL}',
        'content': {'parts': [{'text': (text or '')[:9000]}]},
        'taskType': task_type,
    }
    wait = _RETRY_BASE
    for attempt in range(_RETRY_MAX + 1):
        try:
            r = requests.post(url, json=body, timeout=30)
            if r.status_code == 429:
                # honour Retry-After header if present, otherwise back off
                retry_after = float(r.headers.get('Retry-After', wait))
                sleep_for = max(retry_after, wait)
                if attempt < _RETRY_MAX:
                    time.sleep(sleep_for)
                    wait = min(wait * 2, 60)
                    continue
                r.raise_for_status()          # will raise after final attempt
            r.raise_for_status()
            return r.json()['embedding']['values']
        except requests.exceptions.HTTPError:
            raise
        except Exception as e:
            if attempt < _RETRY_MAX:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            raise RuntimeError(f"embed_text failed after {_RETRY_MAX} retries: {e}")
    raise RuntimeError("embed_text: exhausted retries")


def embed_batch(texts: list, task_type: str = 'RETRIEVAL_DOCUMENT') -> list:
    """
    Embed a list of texts, one request per item with rate-limit throttling.

    gemini-embedding-001 does not expose batchEmbedContents so we loop
    sequentially. A small inter-request delay (_EMBED_DELAY) keeps us
    well under the 1 500 RPM free-tier quota, and embed_text() retries
    automatically on 429 with exponential back-off.
    """
    if not texts:
        return []
    out = []
    for i, t in enumerate(texts):
        try:
            out.append(embed_text(t, task_type=task_type))
        except Exception as e:
            raise RuntimeError(f"Embedding failed on chunk {i}: {e}")
        # pace requests — skip the delay after the very last item
        if i < len(texts) - 1:
            time.sleep(_EMBED_DELAY)
    return out


# ─── Chat generation ─────────────────────────────────────────────────
def generate(
    system_prompt: str,
    user_prompt: str,
    history: list = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """
    Generate a grounded completion.

    history: list of {'role': 'user'|'model', 'text': '...'} alternating
    """
    contents = []
    if history:
        for msg in history:
            role = msg.get('role', 'user')
            if role not in ('user', 'model'):
                role = 'user'
            contents.append({
                'role': role,
                'parts': [{'text': str(msg.get('text', ''))[:4000]}],
            })
    contents.append({'role': 'user', 'parts': [{'text': user_prompt}]})

    body = {
        'systemInstruction': {'parts': [{'text': system_prompt}]},
        'contents': contents,
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_tokens,
            'topP': 0.95,
        },
        'safetySettings': [
            {'category': 'HARM_CATEGORY_HARASSMENT',       'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_HATE_SPEECH',      'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT','threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT','threshold': 'BLOCK_NONE'},
        ],
    }

    def _call(model: str) -> str:
        url = f"{BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        try:
            cand = data['candidates'][0]
            parts = cand.get('content', {}).get('parts', [])
            return ''.join(p.get('text', '') for p in parts).strip()
        except Exception:
            return ''

    try:
        out = _call(CHAT_MODEL)
        if out:
            return out
    except Exception:
        pass

    # Fallback model
    try:
        return _call(CHAT_MODEL_FALLBACK) or "Je n'ai pas pu generer une reponse."
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")
