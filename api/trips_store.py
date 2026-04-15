"""
Trips & Visa Management Store — Postgres-backed.

Persists persons, dossiers (trip or visa), and per-dossier document
checklists in Postgres tables: persons, dossiers, dossier_docs.
"""
import json
import uuid
from datetime import datetime, date
from typing import Optional

import db

# ─── Checklist templates ──────────────────────────────────────────────
TRIP_DOCS = [
    {"key": "attestation_travail", "name": "Attestation de travail", "icon": "briefcase"},
    {"key": "assurance_voyage",    "name": "Assurance voyage",       "icon": "shield"},
    {"key": "ordre_mission",       "name": "Ordre de mission",       "icon": "send"},
    {"key": "lettre_invitation",   "name": "Lettre d'invitation",    "icon": "mail"},
    {"key": "reservation_hotel",   "name": "Reservation hotel",      "icon": "home"},
    {"key": "billet_avion",        "name": "Billet d'avion",         "icon": "plane"},
    {"key": "timbre_voyage",       "name": "Timbre de voyage",       "icon": "stamp"},
]

VISA_DOCS = [
    {"key": "passeport",           "name": "Passeport (copie)",        "icon": "book"},
    {"key": "photo",               "name": "Photo",                    "icon": "image"},
    {"key": "formulaire_visa",     "name": "Formulaire visa",          "icon": "file"},
    {"key": "assurance",           "name": "Assurance",                "icon": "shield"},
    {"key": "reservations",        "name": "Reservations (hotel+vol)", "icon": "calendar"},
    {"key": "attestation_travail", "name": "Attestation de travail",   "icon": "briefcase"},
    {"key": "ordre_mission",       "name": "Ordre de mission",         "icon": "send"},
    {"key": "preuve_financiere",   "name": "Preuve financiere",        "icon": "dollar"},
]


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.utcnow().isoformat(timespec='seconds')


# ─── Store ────────────────────────────────────────────────────────────
class TripsStore:
    def __init__(self, **_):
        pass  # schema bootstrapped by db_schema.py

    # -- persons ---------------------------------------------------------
    def list_persons(self) -> list:
        rows = db.query("SELECT * FROM persons ORDER BY created_at DESC")
        return [self._person_row(r) for r in rows]

    def get_person(self, pid: str) -> Optional[dict]:
        row = db.one("SELECT * FROM persons WHERE id = %s", (pid,))
        return self._person_row(row) if row else None

    def add_person(self, data: dict) -> dict:
        pid = _new_id()
        first = (data.get('first_name') or '').strip()
        last  = (data.get('last_name') or '').strip()
        name  = f"{first} {last}".strip() or (data.get('name') or '').strip()
        db.execute(
            """INSERT INTO persons (id, name, email, phone, passport_number, nationality, notes, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (pid, name,
             (data.get('email') or '').strip(),
             (data.get('phone') or '').strip(),
             (data.get('passport') or data.get('cin') or '').strip(),
             (data.get('nationality') or '').strip(),
             (data.get('notes') or '').strip(),
             datetime.utcnow()),
        )
        return self.get_person(pid) or {'id': pid}

    def update_person(self, pid: str, patch: dict) -> Optional[dict]:
        sets, params = [], []
        field_map = {
            'first_name': 'name', 'last_name': 'name',  # handled specially
            'email': 'email', 'phone': 'phone',
            'passport': 'passport_number', 'cin': 'passport_number',
            'nationality': 'nationality', 'notes': 'notes',
        }
        # Handle first/last name merge
        if 'first_name' in patch or 'last_name' in patch:
            existing = self.get_person(pid) or {}
            first = (patch.get('first_name') or existing.get('first_name') or '').strip()
            last  = (patch.get('last_name') or existing.get('last_name') or '').strip()
            sets.append("name = %s"); params.append(f"{first} {last}".strip())

        for src, col in field_map.items():
            if col == 'name':
                continue
            if src in patch:
                sets.append(f"{col} = %s"); params.append((patch[src] or '').strip())

        if not sets:
            return self.get_person(pid)
        params.append(pid)
        db.execute(f"UPDATE persons SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get_person(pid)

    def delete_person(self, pid: str) -> bool:
        # CASCADE will clean up dossiers and dossier_docs
        return db.execute("DELETE FROM persons WHERE id = %s", (pid,)) > 0

    # -- dossiers --------------------------------------------------------
    def list_dossiers(self) -> list:
        rows = db.query("SELECT * FROM dossiers ORDER BY created_at DESC")
        return [self._dossier_with_docs(r) for r in rows]

    def get_dossier(self, did: str) -> Optional[dict]:
        row = db.one("SELECT * FROM dossiers WHERE id = %s", (did,))
        return self._dossier_with_docs(row) if row else None

    def create_dossier_with_person(self, data: dict) -> dict:
        """Simplified flow: create person + dossier in one call.

        Accepts: person_name, person_email, person_phone, person_passport,
                 type, destination, deadline, notes
        Returns the dossier with embedded person info and doc checklist.
        """
        person_name = (data.get('person_name') or '').strip()
        if not person_name:
            raise ValueError("Nom de la personne requis.")
        person_data = {
            'name': person_name,
            'email': (data.get('person_email') or '').strip(),
            'phone': (data.get('person_phone') or '').strip(),
            'passport': (data.get('person_passport') or '').strip(),
        }
        person = self.add_person(person_data)
        data['person_id'] = person['id']
        return self.add_dossier(data)

    def add_dossier(self, data: dict) -> dict:
        dtype = data.get('type', 'trip')
        if dtype not in ('trip', 'visa'):
            dtype = 'trip'
        did = _new_id()
        now = datetime.utcnow()
        title = (data.get('title') or '').strip() or ('Voyage' if dtype == 'trip' else 'Visa')
        db.execute(
            """INSERT INTO dossiers (id, person_id, type, destination, purpose,
               start_date, end_date, status, priority, notes, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (did, data.get('person_id', ''), dtype,
             (data.get('destination') or '').strip(),
             title,
             data.get('deadline') or None, None,
             'draft', 'normal',
             (data.get('notes') or '').strip(),
             now, now),
        )
        # Seed checklist documents
        tmpl = TRIP_DOCS if dtype == 'trip' else VISA_DOCS
        for t in tmpl:
            db.execute(
                """INSERT INTO dossier_docs (id, dossier_id, doc_key, label, status, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (_new_id(), did, t['key'], t['name'], 'pending', now),
            )
        return self.get_dossier(did) or {'id': did}

    def update_dossier(self, did: str, patch: dict) -> Optional[dict]:
        sets, params = [], []
        for k in ('destination', 'notes'):
            if k in patch:
                sets.append(f"{k} = %s"); params.append((patch[k] or '').strip())
        if 'title' in patch:
            sets.append("purpose = %s"); params.append((patch['title'] or '').strip())
        if 'deadline' in patch:
            sets.append("start_date = %s"); params.append(patch['deadline'] or None)
        if not sets:
            return self.get_dossier(did)
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(did)
        db.execute(f"UPDATE dossiers SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get_dossier(did)

    def delete_dossier(self, did: str) -> bool:
        return db.execute("DELETE FROM dossiers WHERE id = %s", (did,)) > 0

    def duplicate_dossier(self, did: str, new_person_id: str = None) -> Optional[dict]:
        src = self.get_dossier(did)
        if not src:
            return None
        new_id = _new_id()
        now = datetime.utcnow()
        db.execute(
            """INSERT INTO dossiers (id, person_id, type, destination, purpose,
               start_date, end_date, status, priority, notes, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (new_id, new_person_id or src.get('person_id', ''),
             src.get('type', 'trip'), src.get('destination', ''),
             (src.get('title', '') or '') + ' (Copie)',
             src.get('deadline') or None, None,
             'draft', 'normal', src.get('notes', ''), now, now),
        )
        for doc in src.get('documents', []):
            db.execute(
                """INSERT INTO dossier_docs (id, dossier_id, doc_key, label, status, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (_new_id(), new_id, doc['key'], doc['name'], 'pending', now),
            )
        return self.get_dossier(new_id)

    def update_document(self, did: str, key: str, patch: dict) -> Optional[dict]:
        sets, params = [], []
        if 'status' in patch:
            s = patch['status']
            if s not in ('pending', 'in_progress', 'completed'):
                s = 'pending'
            sets.append("status = %s"); params.append(s)
        if 'notes' in patch:
            sets.append("notes = %s"); params.append(patch['notes'] or '')
        if 'file_name' in patch:
            sets.append("file_name = %s"); params.append(patch['file_name'] or '')
        if 'file_size' in patch:
            sets.append("file_size = %s"); params.append(int(patch.get('file_size') or 0))
        if not sets:
            return None
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.extend([did, key])
        cnt = db.execute(
            f"UPDATE dossier_docs SET {', '.join(sets)} WHERE dossier_id = %s AND doc_key = %s",
            tuple(params),
        )
        if cnt == 0:
            return None
        db.execute("UPDATE dossiers SET updated_at = %s WHERE id = %s", (datetime.utcnow(), did))
        row = db.one("SELECT * FROM dossier_docs WHERE dossier_id = %s AND doc_key = %s", (did, key))
        return self._doc_row(row) if row else None

    # -- overview --------------------------------------------------------
    def get_overview(self) -> dict:
        all_dossiers = self.list_dossiers()
        persons_cnt = (db.one("SELECT COUNT(*) AS cnt FROM persons") or {}).get('cnt', 0)
        total_docs = 0; done_docs = 0
        ready = critical = in_prog = urgent = 0
        today = date.today()
        for dos in all_dossiers:
            docs = dos.get('documents', [])
            n = len(docs); done = sum(1 for x in docs if x.get('status') == 'completed')
            total_docs += n; done_docs += done
            pct = int(round(100 * done / n)) if n else 0
            if pct >= 100:    ready += 1
            elif pct < 30:    critical += 1
            else:             in_prog += 1
            dl = dos.get('deadline') or ''
            if pct < 100 and dl:
                try:
                    delta = (date.fromisoformat(dl) - today).days
                    if 0 <= delta <= 14: urgent += 1
                except Exception: pass
        return {
            'total_persons': persons_cnt,
            'total_dossiers': len(all_dossiers),
            'ready_dossiers': ready, 'critical_dossiers': critical,
            'in_progress_dossiers': in_prog, 'urgent_dossiers': urgent,
            'total_documents': total_docs, 'completed_documents': done_docs,
            'progress_pct': int(round(100 * done_docs / total_docs)) if total_docs else 0,
        }

    # -- row converters (keep legacy JSON shape) --------------------------
    @staticmethod
    def _person_row(r: dict) -> dict:
        if not r: return {}
        name = r.get('name', '')
        parts = name.rsplit(' ', 1)
        return {
            'id': r['id'],
            'first_name': parts[0] if parts else '',
            'last_name': parts[1] if len(parts) > 1 else '',
            'cin': r.get('passport_number', ''),
            'passport': r.get('passport_number', ''),
            'phone': r.get('phone', ''),
            'email': r.get('email', ''),
            'created_at': str(r.get('created_at') or ''),
        }

    def _dossier_with_docs(self, r: dict) -> dict:
        if not r: return {}
        did = r['id']
        doc_rows = db.query(
            "SELECT * FROM dossier_docs WHERE dossier_id = %s ORDER BY id",
            (did,),
        )
        tmpl_lookup = {t['key']: t for t in TRIP_DOCS + VISA_DOCS}
        documents = []
        for d in doc_rows:
            tmpl = tmpl_lookup.get(d.get('doc_key'), {})
            documents.append(self._doc_row(d, tmpl))
        # Embed person info
        person_id = r.get('person_id', '')
        person = {}
        if person_id:
            prow = db.one("SELECT * FROM persons WHERE id = %s", (person_id,))
            if prow:
                person = self._person_row(prow)

        # Compute progress
        total = len(documents)
        done = sum(1 for d in documents if d.get('status') == 'completed')
        progress = int(round(100 * done / total)) if total else 0

        return {
            'id': did,
            'person_id': person_id,
            'person': person,
            'type': r.get('type', 'trip'),
            'title': r.get('purpose', ''),
            'destination': r.get('destination', ''),
            'deadline': str(r.get('start_date') or ''),
            'notes': r.get('notes', ''),
            'documents': documents,
            'progress': progress,
            'total_docs': total,
            'completed_docs': done,
            'created_at': str(r.get('created_at') or ''),
            'updated_at': str(r.get('updated_at') or ''),
        }

    @staticmethod
    def _doc_row(r: dict, tmpl: dict = None) -> dict:
        if not r: return {}
        tmpl = tmpl or {}
        return {
            'key': r.get('doc_key', ''),
            'name': r.get('label', '') or tmpl.get('name', ''),
            'icon': tmpl.get('icon', 'file'),
            'status': r.get('status', 'pending'),
            'file_name': r.get('file_name', ''),
            'file_size': r.get('file_size') or 0,
            'notes': r.get('notes', ''),
            'updated_at': str(r.get('updated_at') or ''),
        }
