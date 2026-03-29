#!/usr/bin/env python3
"""
Test script for the HR Document Generator Web App
"""
import sys
import os

# Add the web_app directory to the path
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """Test that all required modules can be imported"""
    try:
        from app import app
        print("✅ Flask app imported successfully")

        from src.core.template_handler import TemplateHandler
        print("✅ TemplateHandler imported successfully")

        from src.core.reference_manager import ReferenceManager
        print("✅ ReferenceManager imported successfully")

        from src.core.excel_importer import ExcelImporter
        print("✅ ExcelImporter imported successfully")

        from src.models.models import Employee, Company, DocumentConfig
        print("✅ Models imported successfully")

        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_app_creation():
    """Test that the Flask app can be created"""
    try:
        from app import app
        with app.app_context():
            print("✅ Flask app context created successfully")
        return True
    except Exception as e:
        print(f"❌ App creation error: {e}")
        return False

def test_company_config():
    """Test that company configuration can be loaded"""
    try:
        from app import load_company_data
        company = load_company_data()
        print(f"✅ Company config loaded: {company['name']}")
        return True
    except Exception as e:
        print(f"❌ Company config error: {e}")
        return False

if __name__ == "__main__":
    print("Testing HR Document Generator Web App...")
    print("=" * 50)

    tests = [
        ("Module Imports", test_imports),
        ("Flask App Creation", test_app_creation),
        ("Company Configuration", test_company_config),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"\n🧪 Running: {test_name}")
        if test_func():
            passed += 1
        else:
            print(f"❌ {test_name} failed")

    print("\n" + "=" * 50)
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! The web app is ready to run.")
        print("\nTo start the web app:")
        print("  python app.py")
        print("  or")
        print("  python run_web_app.bat")
    else:
        print("⚠️  Some tests failed. Please check the errors above.")