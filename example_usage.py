"""
Sample script demonstrating the HR Document Generator API
Shows how to programmatically generate documents without GUI
"""
from datetime import datetime
from pathlib import Path
from src.models.models import Employee, Company, DocumentConfig
from src.core.template_handler import TemplateHandler
from src.core.reference_manager import ReferenceManager

def main():
    print("=" * 60)
    print("HR Document Generator - Programmatic Example")
    print("=" * 60)

    # Initialize reference manager
    rm = ReferenceManager()
    print(f"\n📚 Reference Manager initialized")
    print(f"   Current year prefix: {rm.get_year_prefix()}")
    print(f"   Current counter: {rm.get_counter()}")
    print(f"   Next reference: {rm.get_next_reference()}")

    # Create employee data
    employee = Employee(
        name="Malak CHARFEDDINE",
        gender="Madame",
        cin="12345678",
        cin_place="Tunis",
        cin_date=datetime(2020, 1, 15),
        position="Développeuse Senior",
        start_date=datetime(2023, 3, 1)
    )

    # Create company data
    company = Company(
        name="PLW Tunisia",
        legal_id="1234567",
        representative_name="***REDACTED***",
        address="123 Rue de la République, Tunis",
        city="Tunis"
    )

    print(f"\n👤 Employee: {employee.name}")
    print(f"   Gender: {employee.gender}")
    print(f"   CIN: {employee.cin}")
    print(f"   Position: {employee.position}")

    print(f"\n🏢 Company: {company.name}")
    print(f"   Legal ID: {company.legal_id}")
    print(f"   Representative: {company.representative_name}")

    # Generate Attestation de Travail
    print("\n" + "=" * 60)
    print("Generating Attestation de Travail...")
    print("=" * 60)

    reference = rm.generate_reference()
    config = DocumentConfig(
        employee=employee,
        company=company,
        reference=reference,
        document_type="attestation"
    )

    handler = TemplateHandler()
    output_file = f"output/Attestation_{employee.name.replace(' ', '_')}_{reference.replace('/', '-')}.docx"

    try:
        result = handler.create_attestation_document(config, output_file)
        print(f"✅ Attestation generated successfully!")
        print(f"   Reference: {reference}")
        print(f"   Output file: {result}")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Generate Ordre de Mission
    print("\n" + "=" * 60)
    print("Generating Ordre de Mission...")
    print("=" * 60)

    reference = rm.generate_reference()
    config = DocumentConfig(
        employee=employee,
        company=company,
        reference=reference,
        document_type="ordre_mission"
    )

    try:
        output_file = f"output/Ordre_{employee.name.replace(' ', '_')}_{reference.replace('/', '-')}.docx"
        result = handler.create_ordre_mission_document(config, output_file)
        print(f"✅ Ordre de Mission generated successfully!")
        print(f"   Reference: {reference}")
        print(f"   Output file: {result}")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Show reference stats
    print("\n" + "=" * 60)
    print("Reference Manager Statistics")
    print("=" * 60)
    print(f"Last reference: {rm.get_last_reference()}")
    print(f"Total documents: {rm.get_counter()}")

    print("\n✅ All samples completed successfully!")

if __name__ == "__main__":
    main()
