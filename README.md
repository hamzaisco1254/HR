# HR Document Generator - PLW Tunisia

A professional desktop application for generating HR documents (Attestation de Travail and Ordre de Mission) with a user-friendly PyQt6 interface.

## Features

✅ **Attestation de Travail** - Generate official work certificates
✅ **Ordre de Mission** - Generate mission orders  
✅ **Auto-Incrementing References** - Smart reference numbering with persistence  
✅ **Excel Import** - Load employee data from Excel files  
✅ **Template-Based Generation** - Precise document formatting  
✅ **French Localization** - Complete French interface and date formatting  
✅ **Professional Output** - Production-ready DOCX files  

## Project Structure

```
.
├── main.py                          # Entry point
├── requirements.txt                 # Python dependencies
├── src/
│   ├── __init__.py
│   ├── ui/                          # User interface (PyQt6)
│   │   ├── __init__.py
│   │   └── main_window.py           # Main application window
│   ├── core/                        # Business logic
│   │   ├── __init__.py
│   │   ├── reference_manager.py     # Reference number management
│   │   ├── template_handler.py      # Document generation
│   │   └── excel_importer.py        # Excel data import
│   └── models/                      # Data models
│       ├── __init__.py
│       └── models.py                # Employee, Company, DocumentConfig
├── templates/                       # Document templates (generated)
├── output/                          # Generated documents
├── config/                          # Configuration files
│   └── references.json              # Reference history
└── .github/
    └── copilot-instructions.md      # Workspace instructions
```

## Installation

### 1. Prerequisites
- Python 3.8+
- pip (Python package manager)

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

**Required packages:**
- PyQt6 (GUI framework)
- python-docx (Word document generation)
- pandas (Data processing)
- openpyxl (Excel support)
- pillow (Image support)

### 3. Run the Application

```bash
python main.py
```

## Usage

### Generating Attestation de Travail

1. **Fill Company Information:**
   - Company Name: `PLW Tunisia` (pre-filled)
   - Legal ID (MF): Your company MF number
   - Representative Name: `Lobna MILED` (pre-filled)
   - Address: Company address
   - City: `Tunis` (pre-filled)

2. **Fill Employee Information:**
   - Employee Name
   - Gender (Madame / Monsieur)
   - CIN (Identity Card Number)
   - CIN Issue Place
   - CIN Issue Date
   - Position/Job Title
   - Employment Start Date

3. **Set Document Reference:**
   - Year Prefix: Automatically set (e.g., 26 for 2026)
   - Counter: Auto-increments with each generation
   - Preview shows next reference

4. **Generate:**
   - Click "Générer Attestation"
   - Document saved to `output/` folder
   - Reference number auto-incremented

### Generating Ordre de Mission

1. **Fill Employee Information:**
   - Employee Name
   - Gender
   - Position
   - Mission Start Date

2. **Click "Générer Ordre de Mission"**
   - Document generated with current reference
   - File saved to `output/` folder

### Loading from Excel

1. Click "Charger depuis Excel"
2. Select an Excel file with employee data
3. Choose employee from table
4. Form auto-fills with selected employee data

**Excel column mapping (automatic):**
- `employee_name` or `name` or `nom` → Employee Name
- `cin` or `identity` → CIN
- `cin_place` → CIN Place
- `position` or `poste` → Position

## Reference System

### How It Works

- References are auto-incremented: `26/0001`, `26/0002`, etc.
- Format: `YY/XXXX` where:
  - `YY` = Year prefix (last 2 digits, e.g., 26 for 2026)
  - `XXXX` = Counter (4 digits, zero-padded)

### Configuration

Edit `config/references.json` to:
- Change year prefix
- Reset counter
- View generation history

**Example:**
```json
{
  "year_prefix": 26,
  "counter": 80,
  "history": [
    {
      "reference": "26/0080",
      "generated_at": "2026-03-23T10:30:00"
    }
  ]
}
```

### UI Controls

- **Year (prefix)**: Modify year in reference (e.g., change to 27 for 2027)
- **Counter**: Manually adjust starting number
- **Preview**: Shows next reference that will be generated
- **Reset**: Reset counter to 0 (confirmation required)

## Generated Documents

### Output Format

All documents are saved to the `output/` folder:

```
Attestation_[EmployeeName]_[Reference].docx
Ordre_[EmployeeName]_[Reference].docx
```

**Example:**
```
Attestation_Malak_CHARFEDDINE_26-0080.docx
Ordre_Malak_CHARFEDDINE_26-0080.docx
```

### Document Content

**Attestation de Travail** includes:
- Company information (name, legal ID, representative)
- Employee information (name, gender, CIN, position)
- Employment start date
- Signature block with representative name

**Ordre de Mission** includes:
- Employee information
- Position
- Mission authorization
- Signature block

## Validation Rules

✓ All fields required before generation
✓ CIN must be numeric
✓ Dates must be valid
✓ No duplicate references (auto-managed)
✓ Error handling with clear messages

## Troubleshooting

### Issue: Application won't start
**Solution:**
```bash
# Verify Python installation
python --version

# Reinstall dependencies
pip install --upgrade -r requirements.txt
```

### Issue: PyQt6 import error
**Solution:**
```bash
pip install PyQt6 --no-cache-dir
```

### Issue: Excel import not working
**Solution:**
```bash
pip install openpyxl pandas --upgrade
```

### Issue: Document generation fails
**Possible causes:**
- Invalid file path (ensure output/ folder exists)
- Insufficient permissions
- Corrupted template

**Solution:**
```bash
# Create output folder if missing
mkdir output
```

## Advanced Features

### Excel Template Export
To export a blank Excel template with required columns structure first import, then export template for distribution.

### Batch Processing
Currently generates one document at a time. For batch operations, prepare Excel file with multiple employees and generate individually.

### PDF Export
To add PDF export capability, install reportlab:
```bash
pip install reportlab
```

## Configuration Files

### config/references.json
- Stores reference counter
- Maintains generation history
- User-editable

### Requirements.txt
- Lists all Python dependencies
- Pin specific versions for reproducibility

## Technical Stack

| Component | Technology |
|-----------|-----------|
| GUI Framework | PyQt6 |
| Document Generation | python-docx |
| Data Processing | pandas, openpyxl |
| Date Handling | Python datetime |
| Persistence | JSON |

## Performance

- Document generation: < 2 seconds
- Excel import: < 1-2 seconds for 100+ rows
- Memory footprint: ~150MB

## Security Notes

⚠️ **Important:**
- Documents contain sensitive employee information
- Store generated files securely
- Restrict access to output folder
- Regular backups of references.json recommended

## Future Enhancements

- [ ] PDF export support
- [ ] Email integration
- [ ] Cloud storage (Google Drive, OneDrive)
- [ ] Digital signatures
- [ ] Custom template upload
- [ ] Batch generation from Excel
- [ ] Multi-language support
- [ ] Database backend for history

## License

Proprietary - PLW Tunisia

## Support

For issues or feature requests, contact the development team.

## Version

Current Version: **1.0.0**  
Release Date: March 23, 2026

---

**Built with ❤️ for PLW Tunisia**
