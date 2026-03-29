from flask import Flask, render_template, request, send_file, flash, redirect, url_for
import os
import tempfile
import json
from datetime import datetime
import sys

# Import local copies of modules
from template_handler import TemplateHandler
from reference_manager import ReferenceManager
from excel_importer import ExcelImporter
from models import Employee, Company, DocumentConfig

app = Flask(__name__)
app.secret_key = 'hr_document_generator_web_secret_key_2024'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(os.path.dirname(__file__), 'output')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Initialize managers
template_handler = TemplateHandler()
reference_manager = ReferenceManager()
excel_importer = ExcelImporter()

# Load company data
def load_company_data():
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'company.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            'name': 'Votre Société',
            'address': 'Adresse de la société',
            'phone': 'Téléphone',
            'email': 'email@societe.com',
            'ice': 'ICE123456789',
            'cnss': 'CNSS123456789'
        }

company_data = load_company_data()

@app.route('/')
def index():
    return render_template('index.html', company=company_data)

@app.route('/attestation', methods=['GET', 'POST'])
def attestation():
    if request.method == 'POST':
        try:
            # Get form data
            employee_data = {
                'nom': request.form.get('nom', ''),
                'prenom': request.form.get('prenom', ''),
                'cin': request.form.get('cin', ''),
                'date_naissance': request.form.get('date_naissance', ''),
                'poste': request.form.get('poste', ''),
                'date_embauche': request.form.get('date_embauche', ''),
                'salaire': request.form.get('salaire', ''),
                'genre': request.form.get('genre', 'M')
            }

            # Handle Excel file upload
            excel_file = request.files.get('excel_file')
            excel_url = request.form.get('excel_url', '').strip()

            if excel_file and excel_file.filename:
                # Save uploaded file
                filename = os.path.join(app.config['UPLOAD_FOLDER'], excel_file.filename)
                excel_file.save(filename)
                excel_importer.load_excel(filename)
                row_data = excel_importer.get_row_data(0)  # Use first row
                employee_data.update(row_data)
            elif excel_url:
                # Process cloud URL
                temp_file = process_cloud_url(excel_url)
                if temp_file:
                    excel_importer.load_excel(temp_file)
                    row_data = excel_importer.get_row_data(0)
                    employee_data.update(row_data)

            # Create employee and company objects
            employee = Employee(
                name=f"{employee_data.get('prenom', '')} {employee_data.get('nom', '')}".strip(),
                gender="Madame" if employee_data.get('genre') == 'F' else "Monsieur",
                cin=employee_data.get('cin', ''),
                cin_place="Tunis",  # Default value
                cin_date=datetime.strptime(employee_data.get('date_naissance', '2000-01-01'), '%Y-%m-%d') if employee_data.get('date_naissance') else datetime(2000, 1, 1),
                position=employee_data.get('poste', ''),
                start_date=datetime.strptime(employee_data.get('date_embauche', datetime.now().strftime('%Y-%m-%d')), '%Y-%m-%d') if employee_data.get('date_embauche') else datetime.now()
            )

            company = Company(
                name=company_data['name'],
                legal_id=company_data.get('ice', ''),
                representative_name="Directeur",  # Default value
                address=company_data['address'],
                city="Tunis"  # Default value
            )

            # Generate reference
            reference = reference_manager.generate_reference('attestation')

            # Create document config
            config = DocumentConfig(employee, company, reference, 'attestation')

            # Generate document
            output_filename = f"attestation_{employee.name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

            template_handler.create_attestation_document(config, output_path)

            flash('Document généré avec succès!', 'success')
            return send_file(output_path, as_attachment=True, download_name=output_filename)

        except Exception as e:
            flash(f'Erreur lors de la génération: {str(e)}', 'error')
            return redirect(url_for('attestation'))

    return render_template('attestation.html', company=company_data)

@app.route('/ordre_mission', methods=['GET', 'POST'])
def ordre_mission():
    if request.method == 'POST':
        try:
            # Get form data
            employee_data = {
                'nom': request.form.get('nom', ''),
                'prenom': request.form.get('prenom', ''),
                'cin': request.form.get('cin', ''),
                'poste': request.form.get('poste', ''),
                'genre': request.form.get('genre', 'M')
            }

            mission_data = {
                'destination': request.form.get('destination', ''),
                'motif': request.form.get('motif', ''),
                'date_depart': request.form.get('date_depart', ''),
                'date_retour': request.form.get('date_retour', ''),
                'moyen_transport': request.form.get('moyen_transport', ''),
                'frais': request.form.get('frais', '')
            }

            # Handle Excel file upload
            excel_file = request.files.get('excel_file')
            excel_url = request.form.get('excel_url', '').strip()

            if excel_file and excel_file.filename:
                filename = os.path.join(app.config['UPLOAD_FOLDER'], excel_file.filename)
                excel_file.save(filename)
                excel_importer.load_excel(filename)
                row_data = excel_importer.get_row_data(0)
                employee_data.update(row_data)
            elif excel_url:
                temp_file = process_cloud_url(excel_url)
                if temp_file:
                    excel_importer.load_excel(temp_file)
                    row_data = excel_importer.get_row_data(0)
                    employee_data.update(row_data)

            # Create employee and company objects
            employee = Employee(
                name=f"{employee_data.get('prenom', '')} {employee_data.get('nom', '')}".strip(),
                gender="Madame" if employee_data.get('genre') == 'F' else "Monsieur",
                cin=employee_data.get('cin', ''),
                cin_place="Tunis",  # Default value
                cin_date=datetime.strptime(employee_data.get('date_naissance', '2000-01-01'), '%Y-%m-%d') if employee_data.get('date_naissance') else datetime(2000, 1, 1),
                position=employee_data.get('poste', ''),
                start_date=datetime.now()  # Default for mission
            )

            company = Company(
                name=company_data['name'],
                legal_id=company_data.get('ice', ''),
                representative_name="Directeur",  # Default value
                address=company_data['address'],
                city="Tunis"  # Default value
            )

            # Generate reference
            reference = reference_manager.generate_reference('ordre_mission')

            # Create document config
            config = DocumentConfig(employee, company, reference, 'ordre_mission')

            # Generate document
            output_filename = f"ordre_mission_{employee.name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

            # Parse mission dates
            mission_start = datetime.strptime(mission_data.get('date_depart', datetime.now().strftime('%Y-%m-%d')), '%Y-%m-%d') if mission_data.get('date_depart') else datetime.now()
            mission_end = datetime.strptime(mission_data.get('date_retour', datetime.now().strftime('%Y-%m-%d')), '%Y-%m-%d') if mission_data.get('date_retour') else datetime.now()

            template_handler.create_ordre_mission_document(
                config, 
                output_path,
                country=mission_data.get('destination', 'Allemagne'),
                nationality="",
                address="",
                mission_end_date=mission_end
            )

            flash('Ordre de mission généré avec succès!', 'success')
            return send_file(output_path, as_attachment=True, download_name=output_filename)

        except Exception as e:
            flash(f'Erreur lors de la génération: {str(e)}', 'error')
            return redirect(url_for('ordre_mission'))

    return render_template('ordre_mission.html', company=company_data)

def process_cloud_url(url):
    """Process cloud URLs similar to desktop app"""
    try:
        import requests
        from urllib.parse import urlparse, parse_qs

        # Normalize URL (simplified version of desktop app logic)
        if 'drive.google.com' in url:
            if '/file/d/' in url:
                file_id = url.split('/file/d/')[1].split('/')[0]
                url = f'https://drive.google.com/uc?export=download&id={file_id}'
            elif 'id=' in url:
                file_id = parse_qs(urlparse(url).query).get('id', [None])[0]
                if file_id:
                    url = f'https://drive.google.com/uc?export=download&id={file_id}'

        elif 'dropbox.com' in url:
            if 'dl=0' in url:
                url = url.replace('dl=0', 'dl=1')

        elif 'docs.google.com/spreadsheets' in url:
            if '/edit' in url:
                url = url.replace('/edit', '/export?format=xlsx')

        # Download file
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()

        # Save to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        with open(temp_file.name, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return temp_file.name

    except Exception as e:
        print(f"Error processing cloud URL: {e}")
        return None

@app.route('/references')
def references():
    refs = reference_manager.get_all_references()
    return render_template('references.html', references=refs)

@app.route('/reset_references', methods=['POST'])
def reset_references():
    reference_manager.reset_counter()
    flash('Compteurs de référence réinitialisés!', 'success')
    return redirect(url_for('references'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)