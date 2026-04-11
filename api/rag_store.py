"""
Lightweight RAG (Retrieval Augmented Generation) store.

Parses uploaded HR/legal documents (PDF, DOCX, TXT, MD), chunks them
into semantically-coherent segments, embeds each chunk via Gemini's
text-embedding-004, and persists everything to a JSON file so it can
be queried later by cosine-similarity top-k search.
"""
import io
import json
import math
import os
import uuid
from datetime import datetime

from gemini_client import embed_batch, embed_text


# ─── Helpers ─────────────────────────────────────────────────────────
def cosine_similarity(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list:
    """Split text into overlapping, paragraph-aware chunks."""
    text = (text or '').strip()
    if not text:
        return []
    # Normalise whitespace but keep paragraph breaks
    paras = [p.strip() for p in text.replace('\r', '').split('\n') if p.strip()]
    chunks = []
    current = ''
    for para in paras:
        if len(current) + len(para) + 1 <= size:
            current = (current + '\n' + para).strip() if current else para
            continue
        if current:
            chunks.append(current)
        if len(para) > size:
            # Hard-split long paragraphs with overlap
            step = max(size - overlap, 200)
            for i in range(0, len(para), step):
                chunks.append(para[i:i + size])
            current = ''
        else:
            current = para
    if current:
        chunks.append(current)
    return chunks


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """Best-effort extraction from PDF / DOCX / TXT / MD."""
    fn = (filename or '').lower()

    if fn.endswith('.pdf'):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            parts = []
            for page in reader.pages:
                try:
                    txt = page.extract_text() or ''
                except Exception:
                    txt = ''
                if txt:
                    parts.append(txt)
            return '\n\n'.join(parts)
        except Exception:
            return ''

    if fn.endswith('.docx'):
        try:
            from docx import Document
            doc = Document(io.BytesIO(file_bytes))
            parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            # Also grab table text
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text and cell.text.strip():
                            parts.append(cell.text.strip())
            return '\n\n'.join(parts)
        except Exception:
            return ''

    if fn.endswith('.txt') or fn.endswith('.md'):
        try:
            return file_bytes.decode('utf-8', errors='ignore')
        except Exception:
            return ''

    return ''


# ─── Store ───────────────────────────────────────────────────────────
CATEGORIES = {
    'convention': 'Convention collective',
    'law':        'Droit du travail',
    'policy':     'Politique interne',
    'contract':   'Contrat / Modele',
    'other':      'Autre',
}


class RagStore:
    def __init__(self, path: str = None):
        if path is None:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(root, 'config', 'rag.json')
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._write({'documents': []})

    def _read(self) -> dict:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                d = json.load(f)
                d.setdefault('documents', [])
                return d
        except Exception:
            return {'documents': []}

    def _write(self, data: dict) -> None:
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    # -- Document management --------------------------------------------
    def list_documents(self) -> list:
        d = self._read()
        return [
            {
                'id': doc['id'],
                'filename': doc['filename'],
                'category': doc.get('category', 'other'),
                'category_label': CATEGORIES.get(doc.get('category', 'other'), 'Autre'),
                'size': doc.get('size', 0),
                'chunks': len(doc.get('chunks', [])),
                'created_at': doc.get('created_at', ''),
            }
            for doc in d['documents']
        ]

    def add_document(self, filename: str, file_bytes: bytes, category: str = 'other') -> dict:
        text = extract_text_from_file(file_bytes, filename)
        if not text or len(text.strip()) < 30:
            raise ValueError(
                "Texte non extrait. Le fichier est vide, protege ou au format image."
            )

        chunks = chunk_text(text, size=900, overlap=120)
        if not chunks:
            raise ValueError("Aucun contenu indexable trouve.")

        # Batch-embed (max 100 per call)
        all_embeddings = []
        try:
            for i in range(0, len(chunks), 100):
                batch = chunks[i:i + 100]
                all_embeddings.extend(embed_batch(batch, task_type='RETRIEVAL_DOCUMENT'))
        except Exception as e:
            raise RuntimeError(f"Echec de l'indexation (embeddings) : {e}")

        if len(all_embeddings) != len(chunks):
            raise RuntimeError("Incoherence : nombre d'embeddings != nombre de chunks.")

        doc = {
            'id': uuid.uuid4().hex[:10],
            'filename': filename,
            'category': category if category in CATEGORIES else 'other',
            'size': len(file_bytes),
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'chunks': [
                {'idx': i, 'text': c, 'embedding': e}
                for i, (c, e) in enumerate(zip(chunks, all_embeddings))
            ],
        }
        d = self._read()
        d['documents'].append(doc)
        self._write(d)
        return {
            'id': doc['id'],
            'chunks': len(chunks),
            'filename': filename,
            'category': doc['category'],
        }

    def delete_document(self, doc_id: str) -> bool:
        d = self._read()
        before = len(d['documents'])
        d['documents'] = [x for x in d['documents'] if x.get('id') != doc_id]
        self._write(d)
        return len(d['documents']) < before

    def stats(self) -> dict:
        d = self._read()
        total_chunks = sum(len(x.get('chunks', [])) for x in d['documents'])
        by_cat = {}
        for x in d['documents']:
            c = x.get('category', 'other')
            by_cat[c] = by_cat.get(c, 0) + 1
        return {
            'total_documents': len(d['documents']),
            'total_chunks': total_chunks,
            'by_category': by_cat,
        }

    # -- Retrieval ------------------------------------------------------
    def search(self, query: str, top_k: int = 5) -> list:
        d = self._read()
        if not d['documents'] or not query.strip():
            return []
        try:
            q_emb = embed_text(query, task_type='RETRIEVAL_QUERY')
        except Exception:
            return []
        scored = []
        for doc in d['documents']:
            for chunk in doc.get('chunks', []):
                sim = cosine_similarity(q_emb, chunk.get('embedding', []))
                scored.append({
                    'doc_id': doc['id'],
                    'filename': doc['filename'],
                    'category': doc.get('category', 'other'),
                    'category_label': CATEGORIES.get(
                        doc.get('category', 'other'), 'Autre'),
                    'chunk_idx': chunk.get('idx', 0),
                    'text': chunk.get('text', ''),
                    'score': sim,
                })
        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_k]
