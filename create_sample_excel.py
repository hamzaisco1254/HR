"""
Create sample Excel file for testing import functionality
"""
import pandas as pd
from datetime import datetime

def create_sample_excel():
    """Create a sample Excel file with employee data"""
    
    data = {
        'employee_name': [
            'Malak CHARFEDDINE',
            'Yasmine BENNANI',
            'Karim MANSOURI',
            'Fatima ZOUAOUI',
            'Ahmed HABIB'
        ],
        'gender': [
            'Madame',
            'Madame',
            'Monsieur',
            'Madame',
            'Monsieur'
        ],
        'cin': [
            '12345678',
            '87654321',
            '11223344',
            '55667788',
            '99887766'
        ],
        'cin_place': [
            'Tunis',
            'Sfax',
            'Sousse',
            'Tunis',
            'Ben Arous'
        ],
        'cin_date': [
            datetime(2018, 5, 10),
            datetime(2019, 8, 15),
            datetime(2020, 1, 20),
            datetime(2017, 3, 5),
            datetime(2021, 6, 12)
        ],
        'position': [
            'Développeuse Senior',
            'Chef de Projet',
            'Développeur Backend',
            'Spécialiste RH',
            'Responsable IT'
        ],
        'start_date': [
            datetime(2023, 3, 1),
            datetime(2022, 6, 15),
            datetime(2023, 1, 10),
            datetime(2021, 9, 1),
            datetime(2022, 2, 15)
        ]
    }
    
    df = pd.DataFrame(data)
    output_path = 'samples/employees_sample.xlsx'
    
    # Create samples directory if it doesn't exist
    import os
    os.makedirs('samples', exist_ok=True)
    
    df.to_excel(output_path, index=False)
    print(f"✅ Sample Excel file created: {output_path}")
    print(f"\nPreview (first 3 rows):")
    print(df.head(3).to_string())
    return output_path

if __name__ == "__main__":
    create_sample_excel()
