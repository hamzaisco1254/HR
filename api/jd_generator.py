"""
Job Description generator.

Pipeline:
  1. Query past JDs in the RAG store (category='job_description') using
     a synthesized query built from the user's inputs.
  2. Take the top-N most similar past JDs as few-shot examples.
  3. Build a structured prompt asking Gemini to output JSON with named
     sections (title, summary, responsibilities, ...).
  4. Parse + validate the JSON. Return both the parsed structure and
     the list of source documents used.

Style transfer happens implicitly: by feeding the model the user's own
past JDs as references, the generated output picks up their phrasing,
typical section layout, level of formality, etc.
"""
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import db
from gemini_client import is_configured, generate

logger = logging.getLogger('plw.jd')


# ─── Canonical JD structure ─────────────────────────────────────────

# The keys returned to the UI. Frontend renders each as an editable
# section. Order matters (it's the order they appear in the PDF).
JD_SECTIONS = [
    ('title',            'Titre du poste',          'text'),
    ('summary',          'Resume du poste',         'paragraph'),
    ('mission',          'Mission principale',      'paragraph'),
    ('responsibilities', 'Responsabilites cles',    'list'),
    ('requirements',     'Profil recherche',        'list'),
    ('skills_required',  'Competences requises',    'list'),
    ('skills_preferred', 'Competences appreciees',  'list'),
    ('benefits',         'Conditions et avantages', 'list'),
    ('process',          'Processus de recrutement','paragraph'),
]
JD_SECTION_KEYS   = [k for k, _l, _t in JD_SECTIONS]
JD_SECTION_TYPES  = {k: t for k, _l, t in JD_SECTIONS}
JD_SECTION_LABELS = {k: l for k, l, _t in JD_SECTIONS}


VALID_SENIORITY = ('junior', 'mid', 'senior', 'lead', 'manager', 'director')
VALID_CONTRACT  = ('CDI', 'CDD', 'Stage', 'Freelance', 'Alternance')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


# ─── Generation ─────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Tu es un specialiste RH expert en redaction de fiches de poste pour des entreprises de services informatiques.

OBJECTIF :
- Generer une fiche de poste structuree, claire, professionnelle.
- T'inspirer de la phraseologie et de la structure des fiches de poste de reference fournies (style maison).
- Adapter au niveau de seniorite et au departement demandes.

CONTRAINTES :
- Ne JAMAIS inventer de detail commercial sur l'entreprise (taille, clients, chiffre d'affaires) si ce n'est pas dans les references.
- Les listes (responsabilites, requirements, skills, benefits) doivent contenir 3 a 8 elements concrets.
- Eviter le jargon RH vide ("travailler dans une equipe dynamique", "esprit d'equipe", ...) sauf si les references l'utilisent — adapter au style maison.
- Ecrire en francais clair, ton professionnel mais pas guinde.

FORMAT DE SORTIE :
Tu reponds UNIQUEMENT avec un objet JSON valide aux cles suivantes (toutes obligatoires, listes vides acceptees si non-applicable) :
{
  "title":            "string — intitule complet du poste",
  "summary":          "string — 2-3 phrases qui resument l'opportunite",
  "mission":          "string — 1-2 phrases sur la mission principale",
  "responsibilities": ["string", ...],
  "requirements":     ["string", ...],
  "skills_required":  ["string", ...],
  "skills_preferred": ["string", ...],
  "benefits":         ["string", ...],
  "process":          "string — 1-2 phrases sur le process de recrutement"
}
Ne pas envelopper dans des balises markdown. Pas de commentaires. Pas de prefix/suffix."""


def _strip_fences(text: str) -> str:
    text = (text or '').strip()
    text = re.sub(r'^```[a-z]*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    return text.strip()


def _retrieve_reference_jds(rag_store, position_title: str, department_name: Optional[str],
                            seniority: Optional[str], top_n: int = 3) -> List[Dict]:
    """Build a search query from the user's inputs and pull similar past JDs.

    Filters chunks to category='job_description' only.
    """
    query_parts = [position_title]
    if department_name:
        query_parts.append(department_name)
    if seniority:
        query_parts.append(seniority)
    query = ' / '.join(query_parts)

    # Over-retrieve then filter by category
    try:
        all_hits = rag_store.search(query, top_k=20, threshold=0.0)
    except Exception:
        logger.exception('jd_rag_search_failed')
        return []

    # Group by source doc, keep top score per doc, filter to JD category
    by_doc: Dict[str, Dict] = {}
    for h in all_hits:
        if h.get('category') != 'job_description':
            continue
        doc_id = h.get('doc_id')
        if not doc_id:
            continue
        prev = by_doc.get(doc_id)
        if prev is None or h['score'] > prev['score']:
            # Store the highest-scoring chunk per doc, plus accumulate text
            by_doc[doc_id] = {
                'doc_id':       doc_id,
                'filename':     h['filename'],
                'score':        h['score'],
                'best_chunk':   h['text'],
            }

    # Pull the full text of each top doc (all its chunks concatenated, capped)
    docs = sorted(by_doc.values(), key=lambda d: d['score'], reverse=True)[:top_n]
    for d in docs:
        rows = db.query(
            "SELECT text FROM rag_chunks WHERE doc_id = %s ORDER BY idx",
            (d['doc_id'],),
        )
        full_text = '\n'.join(r['text'] for r in rows)
        # Cap at ~8000 chars per doc to keep prompts manageable
        d['full_text'] = full_text[:8000]
    return docs


def _build_user_prompt(spec: Dict, refs: List[Dict]) -> str:
    """Assemble the user-facing portion of the prompt."""
    ref_block = ''
    if refs:
        parts = []
        for i, r in enumerate(refs, 1):
            parts.append(
                f"════════════ FICHE DE REFERENCE {i} : {r['filename']} ════════════\n"
                f"{r['full_text']}"
            )
        ref_block = '\n\n'.join(parts) + '\n\n'

    pos_title  = spec.get('position_title') or '(non specifie)'
    dept       = spec.get('department_name') or '(non specifie)'
    seniority  = spec.get('seniority') or '(non specifie)'
    contract   = spec.get('contract_type') or 'CDI'
    location   = spec.get('location') or '(a definir)'
    must_haves = spec.get('must_have') or ''
    nice_haves = spec.get('nice_to_have') or ''
    extra_ctx  = spec.get('extra_context') or ''
    tone       = spec.get('tone') or 'professionnel'

    return f"""{ref_block}════════════ DEMANDE ════════════

Generer une fiche de poste avec les parametres suivants :

- Intitule cible    : {pos_title}
- Departement       : {dept}
- Seniorite         : {seniority}
- Type de contrat   : {contract}
- Localisation      : {location}
- Ton attendu       : {tone}

Elements imposes par l'utilisateur (a integrer obligatoirement) :
{must_haves or '  (aucun)'}

Elements bonus (a integrer si pertinent) :
{nice_haves or '  (aucun)'}

Contexte supplementaire :
{extra_ctx or '  (aucun)'}

INSTRUCTIONS :
- Suis le style et la structure des fiches de reference si fournies.
- Si aucune reference n'est fournie, utilise un format standard professionnel.
- Sortie strictement en JSON valide aux cles definies dans les instructions systeme.
"""


def generate_jd(rag_store, spec: Dict) -> Dict:
    """Main entry point.

    spec keys :
      position_title (required)
      department_name, seniority, contract_type, location,
      must_have (multiline str), nice_to_have, extra_context, tone

    Returns:
      {
        content: {title, summary, mission, responsibilities, ...},
        sources: [{doc_id, filename, score}, ...],
        ai_used: bool,
        error:   None | str
      }
    """
    if not spec.get('position_title'):
        raise ValueError("Intitule du poste requis.")

    if not is_configured():
        return {
            'content':  _blank_content(spec.get('position_title', '')),
            'sources':  [],
            'ai_used':  False,
            'error':    'Gemini API non configuree. Edition manuelle requise.',
        }

    refs = _retrieve_reference_jds(
        rag_store,
        position_title=spec['position_title'],
        department_name=spec.get('department_name'),
        seniority=spec.get('seniority'),
        top_n=3,
    )
    user_prompt = _build_user_prompt(spec, refs)

    try:
        raw = generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.5,
            max_tokens=2500,
        )
    except Exception as e:
        logger.exception('jd_generate_failed')
        return {
            'content':  _blank_content(spec['position_title']),
            'sources':  [{'doc_id': r['doc_id'], 'filename': r['filename'], 'score': r['score']} for r in refs],
            'ai_used':  False,
            'error':    f"Erreur Gemini : {e}",
        }

    parsed = _parse_response(raw, fallback_title=spec['position_title'])
    return {
        'content':  parsed,
        'sources':  [{'doc_id': r['doc_id'], 'filename': r['filename'], 'score': round(r['score'], 3)} for r in refs],
        'ai_used':  True,
        'error':    None,
    }


def _blank_content(title: str) -> Dict:
    """Return an empty-but-valid structure when AI is unavailable."""
    return {
        'title':            title,
        'summary':          '',
        'mission':          '',
        'responsibilities': [],
        'requirements':     [],
        'skills_required':  [],
        'skills_preferred': [],
        'benefits':         [],
        'process':          '',
    }


def _parse_response(raw: str, fallback_title: str) -> Dict:
    """Parse the JSON from Gemini, with light recovery for common mistakes."""
    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first {...} block
        m = re.search(r'\{[\s\S]*\}', cleaned)
        if not m:
            return _blank_content(fallback_title)
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return _blank_content(fallback_title)

    out: Dict = {}
    for key in JD_SECTION_KEYS:
        kind = JD_SECTION_TYPES[key]
        v    = data.get(key)
        if kind == 'list':
            if isinstance(v, list):
                out[key] = [str(x).strip() for x in v if str(x).strip()]
            elif isinstance(v, str):
                # Sometimes the model returns a single string instead of a list
                lines = [ln.strip(' -•*\t').strip() for ln in v.split('\n')]
                out[key] = [ln for ln in lines if ln]
            else:
                out[key] = []
        else:
            out[key] = (str(v).strip() if v is not None else '')

    if not out.get('title'):
        out['title'] = fallback_title
    return out


# ─── Drafts persistence ─────────────────────────────────────────────

def _draft_row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    def _dt(v):
        if v is None: return ''
        if hasattr(v, 'isoformat'): return v.isoformat()
        return str(v)
    content = r.get('content') or {}
    # If stored as a JSON string (legacy), parse it
    if isinstance(content, str):
        try: content = json.loads(content)
        except json.JSONDecodeError: content = {}
    return {
        'id':             r['id'],
        'title':          r['title'],
        'department_id':  r.get('department_id'),
        'seniority':      r.get('seniority') or '',
        'location':       r.get('location') or '',
        'contract_type':  r.get('contract_type') or '',
        'content':        content,
        'source_doc_ids': list(r.get('source_doc_ids') or []),
        'created_at':     _dt(r.get('created_at')),
        'updated_at':     _dt(r.get('updated_at')),
    }


def list_drafts() -> List[Dict]:
    rows = db.query(
        """SELECT g.*, d.name AS department_name
             FROM generated_jds g
             LEFT JOIN departments d ON d.id = g.department_id
            ORDER BY g.updated_at DESC""",
    )
    out = []
    for r in rows:
        row = _draft_row(r) or {}
        row['department_name'] = r.get('department_name') or ''
        out.append(row)
    return out


def get_draft(jd_id: str) -> Optional[Dict]:
    if not jd_id:
        return None
    row = db.one(
        """SELECT g.*, d.name AS department_name
             FROM generated_jds g
             LEFT JOIN departments d ON d.id = g.department_id
            WHERE g.id = %s""",
        (jd_id,),
    )
    if not row:
        return None
    out = _draft_row(row) or {}
    out['department_name'] = row.get('department_name') or ''
    return out


def save_draft(data: Dict, jd_id: Optional[str] = None, created_by: Optional[str] = None) -> Dict:
    """Create or update a draft. If jd_id is None, creates a new row."""
    title = (data.get('title') or '').strip()
    if not title and isinstance(data.get('content'), dict):
        title = (data['content'].get('title') or '').strip()
    if not title:
        title = 'Fiche sans titre'

    content = data.get('content') or {}
    if isinstance(content, str):
        try: content = json.loads(content)
        except json.JSONDecodeError: content = {}

    dept   = data.get('department_id') or None
    sen    = (data.get('seniority') or '').strip().lower() or None
    if sen and sen not in VALID_SENIORITY:
        sen = None
    contract = (data.get('contract_type') or '').strip()
    if contract and contract not in VALID_CONTRACT:
        contract = None
    location = (data.get('location') or '').strip() or None
    source_doc_ids = data.get('source_doc_ids') or []
    if not isinstance(source_doc_ids, list):
        source_doc_ids = []

    if jd_id:
        existing = db.one("SELECT id FROM generated_jds WHERE id = %s", (jd_id,))
        if not existing:
            return None
        db.execute(
            """UPDATE generated_jds
                  SET title = %s, department_id = %s, seniority = %s,
                      location = %s, contract_type = %s, content = %s::jsonb,
                      source_doc_ids = %s, updated_at = %s
                WHERE id = %s""",
            (title, dept, sen, location, contract,
             json.dumps(content, ensure_ascii=False), source_doc_ids,
             datetime.utcnow(), jd_id),
        )
    else:
        jd_id = _new_id()
        db.execute(
            """INSERT INTO generated_jds
                 (id, title, department_id, seniority, location, contract_type,
                  content, source_doc_ids, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)""",
            (jd_id, title, dept, sen, location, contract,
             json.dumps(content, ensure_ascii=False), source_doc_ids,
             created_by, datetime.utcnow(), datetime.utcnow()),
        )
    return get_draft(jd_id)


def delete_draft(jd_id: str) -> bool:
    return db.execute("DELETE FROM generated_jds WHERE id = %s", (jd_id,)) > 0
