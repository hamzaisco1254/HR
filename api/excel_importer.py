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
        try:
            self.file_path = Path(file_path)
            self.df = pd.read_excel(file_path)
            return True
        except Exception as e:
            print(f"Error loading Excel file: {e}")
            return False

    def get_columns(self) -> List[str]:
        if self.df is None:
            return []
        return list(self.df.columns)

    def get_rows_count(self) -> int:
        if self.df is None:
            return 0
        return len(self.df)

    def get_all_rows(self) -> List[Dict]:
        if self.df is None:
            return []
        return self.df.to_dict('records')

    def get_row(self, index: int) -> Optional[Dict]:
        if self.df is None or index >= len(self.df):
            return None
        return self.df.iloc[index].to_dict()

    def get_row_data(self, index: int) -> Dict:
        row = self.get_row(index)
        return row if row else {}

    def get_column_values(self, column_name: str) -> List:
        if self.df is None or column_name not in self.df.columns:
            return []
        return self.df[column_name].tolist()

    def search_employee(self, name: str) -> List[Dict]:
        if self.df is None:
            return []
        for col in self.df.columns:
            if 'name' in col.lower() or 'nom' in col.lower():
                matches = self.df[self.df[col].str.contains(name, case=False, na=False)]
                return matches.to_dict('records')
        return []

    def map_columns(self, mapping: Dict[str, str]) -> bool:
        if self.df is None:
            return False
        try:
            rename_dict = {old: new for old, new in mapping.items() if old in self.df.columns}
            self.df.rename(columns=rename_dict, inplace=True)
            return True
        except Exception as e:
            print(f"Error mapping columns: {e}")
            return False

    def get_sample_rows(self, num_rows: int = 5) -> List[Dict]:
        if self.df is None:
            return []
        return self.df.head(num_rows).to_dict('records')
