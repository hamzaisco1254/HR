"""
Build the static RAG bundle for the Mr Hamza chatbot.

Reads PDFs declared in api/reference_docs/manifest.json, extracts and
chunks their text, embeds each chunk via Gemini, then writes a single
gzipped JSON bundle at api/static_rag_bundle.json.gz.

The bundle is loaded at runtime by api/rag_store.py and merged with
DB-stored chunks at search time, so these reference docs are always
available to the chatbot regardless of DB state.

Run locally before each deploy whenever reference docs change:
    set GEMINI_API_KEY=...
    python scripts/build_rag_bundle.py
"""
import gzip
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Make api/ importable so we can reuse extract_text_from_file, chunk_text,
# and embed_batch instead of re-implementing them.
ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / 'api'
sys.path.insert(0, str(API_DIR))

from rag_store import extract_text_from_file, chunk_text  # noqa: E402
from gemini_client import embed_batch, EMBED_MODEL, is_configured  # noqa: E402


REF_DIR = API_DIR / 'reference_docs'
MANIFEST = REF_DIR / 'manifest.json'
OUTPUT = API_DIR / 'static_rag_bundle.json.gz'

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
MAX_CHUNKS_PER_DOC = 500
EMBED_DIM = 3072  # gemini-embedding-001 default


def main() -> int:
    if not is_configured():
        print('ERROR: GEMINI_API_KEY env var not set.', file=sys.stderr)
        return 2

    if not MANIFEST.exists():
        print(f'ERROR: manifest not found at {MANIFEST}', file=sys.stderr)
        return 2

    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if not isinstance(manifest, list) or not manifest:
        print('ERROR: manifest must be a non-empty JSON array.', file=sys.stderr)
        return 2

    documents = []
    total_chunks = 0
    started = time.time()

    for entry in manifest:
        doc_id = entry['id']
        filename = entry['file']
        title = entry.get('title', filename)
        category = entry.get('category', 'other')
        path = REF_DIR / filename

        if not path.exists():
            print(f'  ! skipping {doc_id}: file not found ({path})', file=sys.stderr)
            continue

        size = path.stat().st_size
        print(f'\n>> {doc_id}  ({filename}, {size:,} bytes)')

        blob = path.read_bytes()
        text = extract_text_from_file(blob, filename)
        if not text or len(text.strip()) < 30:
            print(f'  ! skipping {doc_id}: text extraction failed or empty', file=sys.stderr)
            continue
        print(f'  extracted {len(text):,} chars of text')

        chunks = chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        if len(chunks) > MAX_CHUNKS_PER_DOC:
            ratio = len(chunks) / MAX_CHUNKS_PER_DOC
            new_size = min(int(CHUNK_SIZE * ratio), 4000)
            new_overlap = min(int(new_size * 0.12), 300)
            chunks = chunk_text(text, size=new_size, overlap=new_overlap)[:MAX_CHUNKS_PER_DOC]
            print(f'  rechunked to {len(chunks)} chunks (size={new_size}, overlap={new_overlap})')
        else:
            print(f'  produced {len(chunks)} chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})')

        if not chunks:
            print(f'  ! skipping {doc_id}: no chunks produced', file=sys.stderr)
            continue

        print(f'  embedding {len(chunks)} chunks via Gemini...')
        t0 = time.time()
        embeddings = embed_batch(chunks, task_type='RETRIEVAL_DOCUMENT')
        print(f'  done in {time.time() - t0:.1f}s')

        if len(embeddings) != len(chunks):
            print(f'  ! aborting {doc_id}: embeddings/chunks count mismatch', file=sys.stderr)
            continue

        documents.append({
            'id': doc_id,
            'filename': filename,
            'title': title,
            'category': category,
            'size': size,
            'chunks': [
                {'idx': i, 'text': chunk, 'embedding': emb}
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
            ],
        })
        total_chunks += len(chunks)

    if not documents:
        print('ERROR: no documents were embedded.', file=sys.stderr)
        return 1

    bundle = {
        'version': 1,
        'embed_model': EMBED_MODEL,
        'embed_dim': EMBED_DIM,
        'built_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'documents': documents,
    }

    payload = json.dumps(bundle, ensure_ascii=False).encode('utf-8')
    with gzip.open(OUTPUT, 'wb', compresslevel=9) as f:
        f.write(payload)

    elapsed = time.time() - started
    out_size = OUTPUT.stat().st_size
    print(f'\nOK wrote {OUTPUT}  ({out_size:,} bytes gzipped, {len(payload):,} raw)')
    print(f'  {len(documents)} documents, {total_chunks} chunks, {elapsed:.1f}s total')
    return 0


if __name__ == '__main__':
    sys.exit(main())
