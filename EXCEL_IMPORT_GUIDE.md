# Excel Import Feature Guide

## Overview
The HR Document Generator now supports importing employee data from Excel files for both **Attestation de Travail** and **Ordre de Mission** documents. You can choose to fill data manually OR import from Excel.

## Getting Started

### 1. Using the Sample Template
A sample Excel file is provided at: `samples/employees_template.xlsx`

This template contains 5 sample employees with all required fields:
- **ID** - Unique employee identifier (required for dropdown selection)
- **Nom & Prénom** - Full name
- **Genre** - Gender (Madame/Monsieur)
- **CIN** - National ID number
- **Lieu Délivrance CIN** - CIN issue location
- **Date CIN** - CIN date
- **Poste** - Job position
- **Date de Début** - Start date
- **Nationalité** - Nationality
- **Lieu Naissance** - Birth place
- **Date Naissance** - Birth date

### 2. Creating Your Own Excel File
You can create a new Excel file with your employee data. The file must include:
- An **ID** column (required)
- Any or all of the columns from the template

**Column names are flexible** - the system recognizes:
- "ID", "Employee ID", "CIN"
- "Nom & Prénom", "Nom", "Name"
- "Genre", "Gender"
- "Adresse", "Address"
- "Date Naissance", "Birth Date"
- "Date CIN"
- And more...

## How to Use

### For Attestation de Travail:
1. Navigate to the **Attestation** tab
2. In the "Importer depuis Excel (Optionnel)" section:
   - Click **"Parcourir..."** to select your Excel file
   - A success message will show how many employees were loaded
3. Select an employee from the **"Sélectionner Employé par ID"** dropdown
4. The form will automatically populate with the employee's data
5. You can manually edit any fields as needed
6. Click **"Générer Attestation"** to create the document

### For Ordre de Mission:
1. Navigate to the **Ordre de Mission** tab
2. Repeat the same steps as above
3. The system will use the "Destination Mission" field for country selection
4. Click **"Générer Ordre de Mission"** to create the document

## Key Features

✅ **Employee ID Selection** - Dropdown list of all employees for quick selection
✅ **Auto-population** - All relevant fields fill automatically
✅ **Manual Editing** - Edit any field after import
✅ **Flexible Columns** - Column names don't have to match exactly
✅ **Both Document Types** - Works for Attestation and Ordre de Mission
✅ **Optional** - You can still fill data manually if needed

## Sample Workflow

1. Download `samples/employees_template.xlsx`
2. Add your employees to the spreadsheet (keep the ID column)
3. Save the file
4. In the app, click "Parcourir..." and select your file
5. Choose an employee from the dropdown
6. Review/edit the data if needed
7. Click Generate!

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No ID column found" | Make sure your Excel file has a column named "ID" or "CIN" |
| Employee dropdown is empty | Check that your Excel file has an ID column and data rows |
| Fields not populating | Column names might not match. Check the supported column names above |
| Date format issue | Dates should be in DD/MM/YYYY format or YYYY-MM-DD format |

## Column Name Examples

Your Excel file can use any of these column names (not case-sensitive):

```
Employee Names: Nom, Nom & Prénom, Name, Employee Name
Gender: Genre, Gender  
ID: ID, Employee ID, CIN (any of these will work)
Dates: Date Naissance, Birth Date, Lieu Naissance, Birth Place
Location: Adresse, Address
Job: Poste, Position
```

---

**Tip:** Keep a master Excel file of all your employees with all possible fields. Reuse it for any new documents!
