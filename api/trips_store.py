"""
Trips & Visa Management Store.

Persists persons, dossiers (trip or visa), and per-dossier document
checklists to a JSON file under config/trips.json.

Each dossier tracks a dynamic checklist (status, notes, optional file
metadata, timestamp) so the UI can render progress bars, kanban boards
and multi-person overviews.
"""
import json
import os
import uuid
from datetime import datetime, date


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
    {"key": "passeport",         "name": "Passeport (copie)",        "icon": "book"},
    {"key": "photo",             "name": "Photo",                    "icon": "image"},
    {"key": "formulaire_visa",   "name": "Formulaire visa",          "icon": "file"},
    {"key": "assurance",         "name": "Assurance",                "icon": "shield"},
    {"key": "reservations",      "name": "Reservations (hotel+vol)", "icon": "calendar"},
    {"key": "attestation_travail","name": "Attestation de travail",  "icon": "briefcase"},
    {"key": "ordre_mission",     "name": "Ordre de mission",         "icon": "send"},
    {"key": "preuve_financiere", "name": "Preuve financiere",        "icon": "dollar"},
]


# ─── Store ────────────────────────────────────────────────────────────
class TripsStore:
    def __init__(self, path: str = None):
        if path is None:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(root, 'config', 'trips.json')
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._write({'persons': [], 'dossiers': []})

    # -- io --------------------------------------------------------------
    def _read(self) -> dict:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                d = json.load(f)
                d.setdefault('persons', [])
                d.setdefault('dossiers', [])
                return d
        except Exception:
            return {'persons': [], 'dossiers': []}

    def _write(self, data: dict) -> None:
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:10]

    # -- persons ---------------------------------------------------------
    def list_persons(self) -> list:
        return self._read()['persons']

    def get_person(self, pid: str) -> dict | None:
        for p in self.list_persons():
            if p.get('id') == pid:
                return p
        return None

    def add_person(self, data: dict) -> dict:
        d = self._read()
        person = {
            'id': self._new_id(),
            'first_name': (data.get('first_name') or '').strip(),
            'last_name': (data.get('last_name') or '').strip(),
            'cin': (data.get('cin') or '').strip(),
            'passport': (data.get('passport') or '').strip(),
            'phone': (data.get('phone') or '').strip(),
            'email': (data.get('email') or '').strip(),
            'created_at': self._now(),
        }
        d['persons'].append(person)
        self._write(d)
        return person

    def update_person(self, pid: str, patch: dict) -> dict | None:
        d = self._read()
        for p in d['persons']:
            if p.get('id') == pid:
                for k in ('first_name', 'last_name', 'cin', 'passport', 'phone', 'email'):
                    if k in patch:
                        p[k] = (patch.get(k) or '').strip()
                self._write(d)
                return p
        return None

    def delete_person(self, pid: str) -> bool:
        d = self._read()
        before = len(d['persons'])
        d['persons'] = [p for p in d['persons'] if p.get('id') != pid]
        # Cascade: remove dossiers for this person
        d['dossiers'] = [x for x in d['dossiers'] if x.get('person_id') != pid]
        self._write(d)
        return len(d['persons']) < before

    # -- dossiers --------------------------------------------------------
    def list_dossiers(self) -> list:
        return self._read()['dossiers']

    def get_dossier(self, did: str) -> dict | None:
        for dos in self.list_dossiers():
            if dos.get('id') == did:
                return dos
        return None

    def _build_docs(self, dtype: str) -> list:
        tmpl = TRIP_DOCS if dtype == 'trip' else VISA_DOCS
        return [
            {
                'key': t['key'],
                'name': t['name'],
                'icon': t.get('icon', 'file'),
                'status': 'pending',   # pending | in_progress | completed
                'file_name': '',
                'file_size': 0,
                'notes': '',
                'updated_at': '',
            }
            for t in tmpl
        ]

    def add_dossier(self, data: dict) -> dict:
        d = self._read()
        dtype = data.get('type', 'trip')
        if dtype not in ('trip', 'visa'):
            dtype = 'trip'
        dossier = {
            'id': self._new_id(),
            'person_id': data.get('person_id', ''),
            'type': dtype,
            'title': (data.get('title') or '').strip() or (
                'Voyage' if dtype == 'trip' else 'Visa'),
            'destination': (data.get('destination') or '').strip(),
            'deadline': (data.get('deadline') or '').strip(),
            'notes': (data.get('notes') or '').strip(),
            'documents': self._build_docs(dtype),
            'created_at': self._now(),
            'updated_at': self._now(),
        }
        d['dossiers'].append(dossier)
        self._write(d)
        return dossier

    def update_dossier(self, did: str, patch: dict) -> dict | None:
        d = self._read()
        for dos in d['dossiers']:
            if dos.get('id') == did:
                for k in ('title', 'destination', 'deadline', 'notes'):
                    if k in patch:
                        dos[k] = (patch.get(k) or '').strip()
                dos['updated_at'] = self._now()
                self._write(d)
                return dos
        return None

    def delete_dossier(self, did: str) -> bool:
        d = self._read()
        before = len(d['dossiers'])
        d['dossiers'] = [x for x in d['dossiers'] if x.get('id') != did]
        self._write(d)
        return len(d['dossiers']) < before

    def duplicate_dossier(self, did: str, new_person_id: str = None) -> dict | None:
        d = self._read()
        src = None
        for dos in d['dossiers']:
            if dos.get('id') == did:
                src = dos
                break
        if not src:
            return None
        dup = json.loads(json.dumps(src))
        dup['id'] = self._new_id()
        dup['created_at'] = self._now()
        dup['updated_at'] = self._now()
        dup['title'] = (dup.get('title') or '') + ' (Copie)'
        if new_person_id:
            dup['person_id'] = new_person_id
        # Clear uploaded file references so the copy starts fresh on uploads
        for doc in dup.get('documents', []):
            doc['file_name'] = ''
            doc['file_size'] = 0
        d['dossiers'].append(dup)
        self._write(d)
        return dup

    def update_document(self, did: str, key: str, patch: dict) -> dict | None:
        d = self._read()
        for dos in d['dossiers']:
            if dos.get('id') != did:
                continue
            for doc in dos.get('documents', []):
                if doc.get('key') == key:
                    for k in ('status', 'notes', 'file_name', 'file_size'):
                        if k in patch:
                            doc[k] = patch[k]
                    if doc.get('status') not in ('pending', 'in_progress', 'completed'):
                        doc['status'] = 'pending'
                    doc['updated_at'] = self._now()
                    dos['updated_at'] = self._now()
                    self._write(d)
                    return doc
        return None

    # -- overview --------------------------------------------------------
    def get_overview(self) -> dict:
        d = self._read()
        persons = d['persons']
        dossiers = d['dossiers']
        total_docs = 0
        done_docs = 0
        ready_dossiers = 0
        critical_dossiers = 0
        in_progress_dossiers = 0
        today = date.today()
        urgent = 0
        for dos in dossiers:
            docs = dos.get('documents', [])
            n = len(docs)
            done = sum(1 for x in docs if x.get('status') == 'completed')
            total_docs += n
            done_docs += done
            pct = int(round(100 * done / n)) if n else 0
            if pct >= 100:
                ready_dossiers += 1
            elif pct < 30:
                critical_dossiers += 1
            else:
                in_progress_dossiers += 1
            # Urgency: deadline within 14 days and not complete
            dl = dos.get('deadline') or ''
            if pct < 100 and dl:
                try:
                    y, m, dd = [int(x) for x in dl.split('-')]
                    delta = (date(y, m, dd) - today).days
                    if 0 <= delta <= 14:
                        urgent += 1
                except Exception:
                    pass
        return {
            'total_persons': len(persons),
            'total_dossiers': len(dossiers),
            'ready_dossiers': ready_dossiers,
            'critical_dossiers': critical_dossiers,
            'in_progress_dossiers': in_progress_dossiers,
            'urgent_dossiers': urgent,
            'total_documents': total_docs,
            'completed_documents': done_docs,
            'progress_pct': int(round(100 * done_docs / total_docs)) if total_docs else 0,
        }
