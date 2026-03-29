"""Company data persistence manager"""
import json
from pathlib import Path
from typing import Optional


class CompanyManager:
    """Manages persistent company data storage"""

    def __init__(self, config_path: str = "config/company.json"):
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load_data()

    def _load_data(self) -> dict:
        """Load company data from JSON file"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return self._default_data()
        return self._default_data()

    def _default_data(self) -> dict:
        """Get default company data"""
        return {
            "name": "PLW Tunisia",
            "legal_id": "",
            "representative_name": "Lobna MILED",
            "address": "",
            "city": "Tunis"
        }

    def _save_data(self):
        """Save company data to JSON file"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_company_data(self) -> dict:
        """Get all company data"""
        return self.data.copy()

    def set_company_data(self, name: str, legal_id: str, representative: str, address: str, city: str):
        """Save company data"""
        self.data = {
            "name": name,
            "legal_id": legal_id,
            "representative_name": representative,
            "address": address,
            "city": city
        }
        self._save_data()

    def get_field(self, field: str) -> str:
        """Get specific field"""
        return self.data.get(field, "")

    def set_field(self, field: str, value: str):
        """Set specific field"""
        self.data[field] = value
        self._save_data()
