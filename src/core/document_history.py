"""Document history manager - tracks generated documents"""
import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class DocumentHistory:
    """Manages the history of generated documents"""

    def __init__(self, history_file: str = "config/document_history.json"):
        self.history_file = Path(history_file)
        self.history_file.parent.mkdir(exist_ok=True)
        self._load_history()

    def _load_history(self):
        """Load history from JSON file"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
            except:
                self.history = []
        else:
            self.history = []

    def _save_history(self):
        """Save history to JSON file"""
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def add_document(self, reference: str, doc_type: str, employee_name: str, file_path: str):
        """Add a new document to history"""
        entry = {
            'reference': reference,
            'doc_type': doc_type,
            'employee_name': employee_name,
            'file_path': str(file_path),
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%d/%m/%Y %H:%M')
        }
        self.history.append(entry)
        self._save_history()

    def get_all_documents(self) -> List[Dict]:
        """Get all documents in history"""
        return self.history

    def get_documents_by_type(self, doc_type: str) -> List[Dict]:
        """Get documents filtered by type"""
        return [doc for doc in self.history if doc['doc_type'] == doc_type]

    def clear_history(self):
        """Clear all history"""
        self.history = []
        self._save_history()

    def get_recent_documents(self, limit: int = 10) -> List[Dict]:
        """Get most recent documents"""
        return sorted(self.history, key=lambda x: x['timestamp'], reverse=True)[:limit]