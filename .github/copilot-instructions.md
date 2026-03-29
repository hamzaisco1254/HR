<!-- VS Code Copilot Customization Instructions -->

# HR Document Generator - Development Instructions

## Project Overview

Python desktop application for generating HR documents (Attestation de Travail, Ordre de Mission) using PyQt6 GUI and template-based document generation.

## Key Technologies

- **Python 3.8+**
- **PyQt6** - Desktop GUI framework
- **python-docx** - DOCX document generation
- **pandas + openpyxl** - Excel import/export
- **JSON** - Configuration persistence

## Project Structure

```
src/
  ├── ui/
  │   └── main_window.py        # PyQt6 GUI - Main application
  ├── core/
  │   ├── template_handler.py   # Document generation engine
  │   ├── reference_manager.py  # Reference numbering system
  │   └── excel_importer.py     # Excel data loading
  └── models/
      └── models.py             # Data classes (Employee, Company)

output/                         # Generated DOCX files
config/                         # Configuration (references.json)
templates/                      # Custom templates directory
```

## Development Workflow

### Setup Instructions

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Verify installation:**
   ```bash
   python -c "from src.ui.main_window import main; print('OK')"
   ```

3. **Run application:**
   ```bash
   python main.py
   ```

### Creating New Documents

When adding new document types:
1. Add method to `TemplateHandler` class
2. Create corresponding tab in UI
3. Add to reference manager if needed
4. Test generation and validation

### Modifying Existing Templates

Document templates are generated programmatically in `template_handler.py`:
- `create_attestation_document()` - Attestation de Travail
- `create_ordre_mission_document()` - Ordre de Mission

To modify document content, edit the body text in these methods.

### Adding Excel Column Mapping

Edit the `field_mapping` dictionary in `AttestationTab.fill_from_row()` to support additional Excel column names.

### Reference System Extension

The `ReferenceManager` supports:
- Auto-incrementing with year prefix
- Custom year management
- Counter reset
- History tracking in JSON

## Common Tasks

### Debug Application
```bash
python -m pdb main.py
```

### Test Excel Import
```python
from src.core.excel_importer import ExcelImporter
importer = ExcelImporter()
importer.load_excel("data.xlsx")
print(importer.get_all_rows())
```

### Generate Single Document
```python
from src.models.models import Employee, Company, DocumentConfig
from src.core.template_handler import TemplateHandler
from datetime import datetime

employee = Employee(...)
company = Company(...)
config = DocumentConfig(employee, company, "26/0001", "attestation")
handler = TemplateHandler()
handler.create_attestation_document(config, "output/test.docx")
```

### Reset References
```python
from src.core.reference_manager import ReferenceManager
rm = ReferenceManager()
rm.reset_counter()
```

## Testing Checklist

- [ ] Form validation works correctly
- [ ] Document generation completes without errors
- [ ] Reference counter increments properly
- [ ] Excel import populates form correctly
- [ ] French date formatting is correct ("23 Mars 2026")
- [ ] Output documents open in Word
- [ ] Gender-specific text adapts (employé/employée)
- [ ] Reference persistence survives app restart

## Known Limitations

1. One document per generation
2. No batch processing from Excel
3. No PDF export (Word fallback)
4. Limited template customization via UI

## Performance Targets

- Document generation: < 2 seconds
- Excel load with 100 rows: < 2 seconds
- Memory usage: < 200MB

## Code Style

- PEP 8 compliant
- Type hints for clarity
- Docstrings for all public methods
- French strings in UI, English in code comments

## Common Issues & Fixes

| Issue | Solution |
|-------|----------|
| PyQt6 import error | `pip install PyQt6 --no-cache-dir` |
| openpyxl not found | `pip install openpyxl` |
| output/ folder missing | `mkdir output` |
| references.json corrupt | Delete file, auto-recreated on next run |

## Contributing Guidelines

1. Keep functions under 50 lines when possible
2. Add docstrings for new functions
3. Test both UI flow and data processing
4. Validate input before document generation
5. Use French for UI strings, English for code

## Documentation Updates

- README.md - User guide and feature list
- copilot-instructions.md - Development guide (this file)
- Code comments - Inline technical details

---

**Last Updated:** March 23, 2026
**Version:** 1.0.0
