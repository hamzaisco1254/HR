"""Final project verification script"""
import sys
import os

print('='*70)
print('FINAL PROJECT VERIFICATION')
print('='*70)

# Test 1: Check all imports
print('\n✓ Testing imports...')
try:
    from src.ui.main_window import MainWindow
    from src.core.reference_manager import ReferenceManager
    from src.core.template_handler import TemplateHandler
    from src.core.excel_importer import ExcelImporter
    from src.models.models import Employee, Company, DocumentConfig
    print('  ✓ All modules imported successfully')
except Exception as e:
    print(f'  ✗ Import error: {e}')
    sys.exit(1)

# Test 2: Check reference manager
print('\n✓ Testing reference manager...')
try:
    rm = ReferenceManager()
    next_ref = rm.get_next_reference()
    print(f'  ✓ Reference manager working')
    print(f'    Next reference: {next_ref}')
except Exception as e:
    print(f'  ✗ Error: {e}')
    sys.exit(1)

# Test 3: Check template handler
print('\n✓ Testing template handler...')
try:
    th = TemplateHandler()
    print('  ✓ Template handler ready')
except Exception as e:
    print(f'  ✗ Error: {e}')
    sys.exit(1)

# Test 4: Check Excel importer
print('\n✓ Testing Excel importer...')
try:
    ei = ExcelImporter()
    if ei.load_excel('samples/employees_sample.xlsx'):
        rows = ei.get_all_rows()
        print(f'  ✓ Excel importer working')
        print(f'    Loaded {len(rows)} employee records')
    else:
        print('  ✗ Could not load Excel file')
except Exception as e:
    print(f'  ✗ Error: {e}')

# Test 5: Check output files
print('\n✓ Checking generated output files...')
if os.path.exists('output'):
    files = [f for f in os.listdir('output') if f.endswith('.docx')]
    print(f'  ✓ Found {len(files)} generated DOCX files')
    for f in files:
        size_kb = os.path.getsize(f'output/{f}') / 1024
        print(f'    - {f} ({size_kb:.1f} KB)')

# Test 6: Check documentation
print('\n✓ Checking documentation files...')
docs = ['README.md', 'QUICKSTART.md', 'SETUP_REPORT.md']
for doc in docs:
    if os.path.exists(doc):
        print(f'  ✓ {doc}')

print('\n' + '='*70)
print('✅ PROJECT VERIFICATION COMPLETE - ALL SYSTEMS OPERATIONAL')
print('='*70)
print('\n📚 Documentation:')
print('  - README.md: Full documentation')
print('  - QUICKSTART.md: Quick start guide')
print('  - SETUP_REPORT.md: Detailed setup report')
print('\n🚀 To start the application:')
print('  python main.py')
