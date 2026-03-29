"""Excel importer - loads employee data from Excel files"""
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime


class ExcelImporter:
    """Handles importing employee data from Excel files"""

    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.file_path: Optional[Path] = None

    def load_excel(self, file_path: str) -> bool:
        """
        Load Excel file into memory

        Args:
            file_path: Path to Excel file

        Returns:
            True if successful, False otherwise
        """
        try:
            self.file_path = Path(file_path)
            self.df = pd.read_excel(file_path)
            return True
        except Exception as e:
            print(f"Error loading Excel file: {e}")
            return False

    def get_columns(self) -> List[str]:
        """Get column names from loaded Excel"""
        if self.df is None:
            return []
        return list(self.df.columns)

    def get_rows_count(self) -> int:
        """Get number of rows in loaded Excel"""
        if self.df is None:
            return 0
        return len(self.df)

    def get_all_rows(self) -> List[Dict]:
        """Get all rows as dictionaries"""
        if self.df is None:
            return []
        return self.df.to_dict('records')

    def get_row_data(self, index: int) -> Dict:
        """
        Get row data by index (alias for get_row)

        Args:
            index: Row index

        Returns:
            Dictionary with row data
        """
        row = self.get_row(index)
        return row if row else {}

    def get_column_values(self, column_name: str) -> List:
        """Get all values from a specific column"""
        if self.df is None or column_name not in self.df.columns:
            return []
        return self.df[column_name].tolist()

    def search_employee(self, name: str) -> List[Dict]:
        """
        Search for employees by name

        Args:
            name: Employee name to search for

        Returns:
            List of matching rows
        """
        if self.df is None:
            return []

        # Try common name column names
        for col in self.df.columns:
            if 'name' in col.lower() or 'nom' in col.lower():
                matches = self.df[self.df[col].str.contains(name, case=False, na=False)]
                return matches.to_dict('records')
        return []

    def map_columns(self, mapping: Dict[str, str]) -> bool:
        """
        Rename columns according to mapping

        Args:
            mapping: Dictionary mapping old column names to new names
                    e.g., {"Name": "employee_name", "ID": "cin"}

        Returns:
            True if successful
        """
        if self.df is None:
            return False

        try:
            rename_dict = {}
            for old_name, new_name in mapping.items():
                if old_name in self.df.columns:
                    rename_dict[old_name] = new_name

            self.df.rename(columns=rename_dict, inplace=True)
            return True
        except Exception as e:
            print(f"Error mapping columns: {e}")
            return False

    def get_sample_rows(self, num_rows: int = 5) -> List[Dict]:
        """Get first N rows for preview"""
        if self.df is None:
            return []
        return self.df.head(num_rows).to_dict('records')

    def export_template(self, output_path: str) -> bool:
        """
        Export a template Excel file with required columns

        Args:
            output_path: Path to save template

        Returns:
            True if successful
        """
        try:
            template_data = {
                'employee_name': ['Nom Prénom'],
                'gender': ['Madame'],
                'cin': ['12345678'],
                'cin_place': ['Tunis'],
                'cin_date': ['2020-01-01'],
                'position': ['Poste'],
                'start_date': ['2023-01-01'],
            }

            df = pd.DataFrame(template_data)
            df.to_excel(output_path, index=False)
            return True
        except Exception as e:
            print(f"Error exporting template: {e}")
            return False
