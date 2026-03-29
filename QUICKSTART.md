# Quick Start Guide - HR Document Generator

## 🚀 Getting Started (5 Minutes)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Application
```bash
python main.py
```

### 3. Generate Your First Document

**Within the PyQt6 GUI:**

1. **Fill in Employee Information:**
   - Name: Enter employee name
   - Gender: Select Madame or Monsieur
   - CIN: Enter identity card number
   - Position: Enter job title

2. **Click "Générer Attestation"**
   - Document generates automatically
   - Saved to `output/` folder
   - Reference number auto-increments

## 📋 Example Workflow

### Terminal (Command Line)
```bash
# Generate document programmatically
python example_usage.py
```

### Load from Excel
```bash
# In the GUI:
# 1. Click "Charger depuis Excel"
# 2. Select "samples/employees_sample.xlsx"
# 3. Choose an employee
# 4. Form auto-fills
# 5. Click "Générer Attestation"
```

## 📁 Project Contents

| File/Folder | Purpose |
|------------|---------|
| `main.py` | Application entry point |
| `src/ui/main_window.py` | PyQt6 graphical interface |
| `src/core/template_handler.py` | Document generation |
| `src/core/reference_manager.py` | Reference numbering |
| `src/core/excel_importer.py` | Excel data loading |
| `output/` | Generated documents |
| `samples/employees_sample.xlsx` | Sample data |
| `config/references.json` | Reference history |

## 🎯 Key Features

✅ **Attestation de Travail** - Official work certificates
✅ **Ordre de Mission** - Employee mission orders  
✅ **Auto-incrementing References** - Smart numbering  
✅ **Excel Import** - Load employee data  
✅ **French Interface** - Complete French UI  
✅ **Professional Output** - Production-ready DOCX  

## 📊 Generated Files

Documents are saved with automatic naming:
```
output/Attestation_[Name]_[Reference].docx
output/Ordre_[Name]_[Reference].docx
```

**Example:**
```
output/Attestation_Malak_CHARFEDDINE_26-0001.docx
output/Ordre_Yasmine_BENNANI_26-0002.docx
```

## 🔧 Customization

### Change Company Info
Edit in the UI under "Données de l'Entreprise":
- Company Name (default: PLW Tunisia)
- Legal ID (MF number)
- Representative (default: ***REDACTED***)
- Address
- City

### Reset Reference Counter
1. In the "Attestation de travail" tab
2. Find "Numérotation de Document" section
3. Click "Réinitialiser" button
4. Confirm the reset

### Change Year Prefix
1. In "Année (prefix)" field
2. Enter new year (e.g., 27 for 2027)
3. Counter auto-updates

## ❓ FAQ

**Q: Where are documents saved?**
A: All documents save to the `output/` folder

**Q: Can I edit generated documents?**
A: Yes! They're standard DOCX files. Open in Microsoft Word or any compatible editor.

**Q: How do I load new employees from Excel?**
A: Click "Charger depuis Excel" and select your Excel file

**Q: What if I need to change the document content?**
A: Edit the template text in `src/core/template_handler.py`

**Q: How do I reset the reference counter?**
A: Use the "Réinitialiser" button in the Reference section

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| ImportError: PyQt6 | Run `pip install PyQt6` |
| ImportError: docx | Run `pip install python-docx` |
| No output folder | Run `mkdir output` (already created) |
| Excel import fails | Ensure Excel file has correct columns |

## 📞 Support

For detailed documentation, see:
- `README.md` - Full documentation
- `copilot-instructions.md` - Development guide
- Code comments in `src/` for technical details

## 🎓 Next Steps

1. Test with sample Excel file: `samples/employees_sample.xlsx`
2. Generate an Attestation
3. Generate an Ordre de Mission
4. Open generated documents in Word
5. Customize company information
6. Modify templates as needed

---

**Ready to generate documents? Run `python main.py` now!** 🚀
