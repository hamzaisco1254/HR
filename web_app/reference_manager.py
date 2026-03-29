"""Reference management system - handles auto-incrementing document references"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class ReferenceManager:
    """Manages document reference generation and persistence"""

    def __init__(self, config_path: str = "config/references.json"):
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load_data()

    def _load_data(self) -> Dict:
        """Load reference data from JSON file"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return self._initialize_data()
        return self._initialize_data()

    def _initialize_data(self) -> Dict:
        """Initialize default reference data"""
        current_year = datetime.now().year % 100  # Get last 2 digits of year
        return {
            "year_prefix": current_year,
            "counter": 0,
            "history": []
        }

    def _save_data(self):
        """Save reference data to JSON file"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def generate_reference(self, year_prefix: Optional[int] = None) -> str:
        """
        Generate next reference number

        Args:
            year_prefix: Optional custom year prefix (e.g., 26 for 2026)

        Returns:
            Reference string (e.g., "26/0081")
        """
        if year_prefix is not None:
            self.data["year_prefix"] = year_prefix

        self.data["counter"] += 1
        reference = f"{self.data['year_prefix']}/{self.data['counter']:04d}"

        # Log to history
        self.data["history"].append({
            "reference": reference,
            "generated_at": datetime.now().isoformat()
        })

        self._save_data()
        return reference

    def get_next_reference(self) -> str:
        """Get the next reference without modifying counter"""
        next_counter = self.data["counter"] + 1
        return f"{self.data['year_prefix']}/{next_counter:04d}"

    def set_counter(self, value: int):
        """Manually set counter value"""
        self.data["counter"] = value
        self._save_data()

    def set_year_prefix(self, year: int):
        """Set year prefix (e.g., 26 for 2026)"""
        self.data["year_prefix"] = year
        self._save_data()

    def reset_counter(self):
        """Reset counter to 0"""
        self.data["counter"] = 0
        self._save_data()

    def get_last_reference(self) -> Optional[str]:
        """Get the last generated reference"""
        if self.data["history"]:
            return self.data["history"][-1]["reference"]
        return None

    def get_counter(self) -> int:
        """Get current counter value"""
        return self.data["counter"]

    def get_year_prefix(self) -> int:
        """Get current year prefix"""
        return self.data["year_prefix"]
