"""Document history manager — Postgres-backed.

Stores history of generated HR documents (attestations, ordres de mission)
in the `doc_history` table. API matches the legacy interface.
"""
import uuid
from datetime import datetime
from typing import List, Dict

import db


class DocumentHistory:
    """Manages the history of generated documents in Postgres."""

    def add_document(self, reference: str, doc_type: str,
                     employee_name: str, file_path: str):
        db.execute(
            """INSERT INTO doc_history (id, reference, doc_type, employee_name, file_path, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (uuid.uuid4().hex[:12], reference, doc_type, employee_name,
             str(file_path), datetime.utcnow()),
        )

    def get_all_documents(self) -> List[Dict]:
        rows = db.query(
            "SELECT id, reference, doc_type, employee_name, file_path, created_at FROM doc_history ORDER BY created_at DESC"
        )
        return [self._row(r) for r in rows]

    def get_documents_by_type(self, doc_type: str) -> List[Dict]:
        rows = db.query(
            "SELECT id, reference, doc_type, employee_name, file_path, created_at FROM doc_history WHERE doc_type = %s ORDER BY created_at DESC",
            (doc_type,),
        )
        return [self._row(r) for r in rows]

    def get_recent_documents(self, limit: int = 10) -> List[Dict]:
        rows = db.query(
            "SELECT id, reference, doc_type, employee_name, file_path, created_at FROM doc_history ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        return [self._row(r) for r in rows]

    def clear_history(self):
        db.execute("DELETE FROM doc_history")

    @staticmethod
    def _row(r: dict) -> dict:
        ts = r.get('created_at')
        return {
            'reference':     r.get('reference', ''),
            'doc_type':      r.get('doc_type', ''),
            'employee_name': r.get('employee_name', ''),
            'file_path':     r.get('file_path', ''),
            'timestamp':     ts.isoformat() if ts else '',
            'date':          ts.strftime('%d/%m/%Y %H:%M') if ts else '',
        }
