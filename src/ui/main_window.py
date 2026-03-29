"""Main PyQt6 application - HR Document Generator UI"""
import sys
import os
from datetime import datetime
from pathlib import Path
import requests
import tempfile
from urllib.parse import quote
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QLineEdit, QComboBox, QDateEdit, QPushButton,
    QTextEdit, QMessageBox, QProgressBar, QFileDialog, QFrame,
    QGridLayout, QGroupBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QScrollArea
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QIcon, QPixmap

from src.models.models import Employee, Company, DocumentConfig
from src.core.reference_manager import ReferenceManager
from src.core.template_handler import TemplateHandler
from src.core.excel_importer import ExcelImporter
from src.core.company_manager import CompanyManager
from src.core.document_history import DocumentHistory


def normalize_cloud_excel_url(url: str) -> str:
    """Convertit les URL cloud vers un téléchargement direct connu"""

    u = url.strip()
    if not u:
        return u

    # Google Sheets: convert to direct Excel export
    if 'docs.google.com/spreadsheets/d/' in u:
        file_id = u.split('/d/')[1].split('/')[0]
        return f'https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx'

    if 'drive.google.com' in u:
        if '/file/d/' in u:
            file_id = u.split('/file/d/')[1].split('/')[0]
            return f'https://drive.google.com/uc?export=download&id={file_id}'
        if 'open?id=' in u:
            file_id = u.split('open?id=')[1].split('&')[0]
            return f'https://drive.google.com/uc?export=download&id={file_id}'
        if 'uc?id=' in u:
            if 'export=download' in u:
                return u
            sep = '&' if '?' in u else '?'
            return f'{u}{sep}export=download'

    if 'dropbox.com' in u:
        if 'dl=0' in u:
            return u.replace('dl=0', 'dl=1')
        if 'dl=1' not in u:
            sep = '&' if '?' in u else '?'
            return f'{u}{sep}dl=1'

    if '1drv.ms' in u or 'onedrive.live.com' in u:
            if 'download=1' not in u:
                return u + ('&' if '?' in u else '?') + 'download=1'
            return u

    # OneDrive for Business personal URLs (msbtn-my.sharepoint.com)
    if '-my.sharepoint.com' in u and ':x:/g/personal/' in u:
        # Format: https://msbtn-my.sharepoint.com/:x:/g/personal/username/itemid...?params
        # Try multiple strategies to get direct download
        
        # Strategy 1: Add multiple download parameters
        base_url = u.split('?')[0]
        download_params = '?download=1&isSPOFile=1&web=1'
        return base_url + download_params

    # SharePoint lien : essayer direct download avec download=1
    if 'sharepoint.com' in u:
        # First, ensure download=1 is present
        if 'download=1' not in u:
            u = u + ('&' if '?' in u else '?') + 'download=1'

        # Try conversion for typical share links (:x:/r/, etc.)
        for pattern in [':x:/r/', ':u:/r/', ':w:/r/', ':p:/r/']:
            if pattern in u:
                base = u.split(pattern)[0]
                file_path = u.split(pattern)[1].split('?')[0]
                source_url = f"{base}/{file_path}"
                # Try _layouts endpoint
                converted = f"{base}/_layouts/15/download.aspx?SourceUrl={quote(source_url, safe='')}"
                return converted

        # Try adding /download.aspx for direct paths
        if '/Shared Documents/' in u and '/_layouts/15/download.aspx' not in u:
            base = u.split('/Shared Documents/')[0]
            filename = u.split('/Shared Documents/')[-1].split('?')[0]
            download_url = f"{base}/_layouts/15/download.aspx?SourceUrl={quote(f'{base}/Shared Documents/{filename}', safe='')}"
            return download_url

        return u
    return u

def download_to_temp_excel(url: str) -> str:
    """Télécharge un fichier Excel depuis une URL cloud en essayant plusieurs stratégies."""
    
    # Multiple strategies with different headers and parameters
    strategies = [
        # Strategy 1: Direct Excel MIME type
        {'url': url, 'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        }},
        # Strategy 2: Add download=1 + office referer
        {'url': url.split('?')[0] + '?download=1&isSPOFile=1', 'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/octet-stream',
            'Referer': 'https://office.com/'
        }},
        # Strategy 3: Generic binary download
        {'url': url, 'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': '*/*'
        }},
        # Strategy 4: Aggressive Excel mimetype + web parameter
        {'url': url.split('?')[0] + '?download=1&web=1', 'headers': {
            'User-Agent': 'Microsoft Excel',
            'Accept': 'application/vnd.ms-excel'
        }},
    ]
    
    last_error = None
    
    for idx, strategy in enumerate(strategies):
        try:
            response = requests.get(strategy['url'], stream=True, timeout=30, 
                                   headers=strategy['headers'], allow_redirects=True)
            response.raise_for_status()
            
            content_type = response.headers.get('content-type', '').lower()
            content_length = len(response.content) if response.content else 0
            
            # Skip HTML responses
            if 'text/html' in content_type:
                last_error = f"Strategy {idx+1}: HTML response"
                continue
            
            # If we got something, try to save it
            if content_length > 100:  # At least 100 bytes
                with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as temp_file:
                    temp_file_path = temp_file.name
                    # Write all content at once since we already fetched it
                    temp_file.write(response.content)
                
                # Verify it's a valid Excel file
                with open(temp_file_path, 'rb') as f:
                    signature = f.read(4)
                
                if signature.startswith(b'PK'):  # ZIP format (xlsx)
                    return temp_file_path
                else:
                    last_error = f"Strategy {idx+1}: Invalid file signature"
                    try:
                        os.unlink(temp_file_path)
                    except:
                        pass
                    continue
            else:
                last_error = f"Strategy {idx+1}: Response too small ({content_length} bytes)"
                continue
                
        except Exception as e:
            last_error = f"Strategy {idx+1}: {str(e)}"
            continue
    
    # All strategies failed - provide helpful error
    error_msg = "❌ Impossible de télécharger le fichier Excel de OneDrive.\n\n"
    error_msg += "Cause probable: OneDrive personnel ne permet pas toujours le partage direct sans authentification.\n\n"
    error_msg += "Solutions:\n"
    error_msg += "1️⃣ Téléchargez le fichier localement et importez-le\n"
    error_msg += "2️⃣ Utilisez Google Drive (partage public) ou Dropbox\n"
    error_msg += "3️⃣ Générez un lien de partage Microsoft avec accès anonyme\n\n"
    error_msg += f"Dernier détail: {last_error}"
    raise ValueError(error_msg)


class DocumentGeneratorThread(QThread):
    """Worker thread for document generation"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, config: DocumentConfig, doc_type: str, output_path: str):
        super().__init__()
        self.config = config
        self.doc_type = doc_type
        self.output_path = output_path

    def run(self):
        try:
            self.progress.emit("Initializing document generator...")
            handler = TemplateHandler()

            self.progress.emit(f"Creating {self.doc_type} document...")
            if self.doc_type == "attestation":
                result = handler.create_attestation_document(self.config, self.output_path)
            else:
                result = handler.create_ordre_mission_document(self.config, self.output_path)

            self.progress.emit(f"Document saved to {result}")
            self.finished.emit(True, result)
        except Exception as e:
            self.progress.emit(f"Error: {str(e)}")
            self.finished.emit(False, str(e))


class AttestationTab(QWidget):
    """Tab for Attestation de Travail generation"""

    def __init__(self, ref_manager: ReferenceManager, history_manager: DocumentHistory):
        super().__init__()
        self.ref_manager = ref_manager
        self.history_manager = history_manager
        self.company_manager = CompanyManager()
        self.excel_data = {}  # Store loaded Excel data indexed by ID
        self.excel_rows = []  # Store all rows for reference
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Define common style for input fields
        input_style = """
            QLineEdit, QDateEdit, QComboBox, QSpinBox {
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 8px;
                background-color: white;
                color: #162142;
                font-size: 11px;
            }
            QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 2px solid #D61E8B;
                background-color: #fff9fc;
            }
            QDateEdit::down-button, QComboBox::down-button {
                border: none;
                background-color: #D61E8B;
                width: 20px;
            }
        """
        self.setStyleSheet(input_style)

        # Company Section
        company_group = QGroupBox("Données de l'Entreprise (Sauvegardées)")
        company_layout = QGridLayout()

        company_layout.addWidget(QLabel("Nom Entreprise:"), 0, 0)
        self.company_name = QLineEdit()
        self.company_name.setText(self.company_manager.get_field("name"))
        company_layout.addWidget(self.company_name, 0, 1)

        company_layout.addWidget(QLabel("Identifiant MF:"), 1, 0)
        self.legal_id = QLineEdit()
        self.legal_id.setText(self.company_manager.get_field("legal_id"))
        company_layout.addWidget(self.legal_id, 1, 1)

        company_layout.addWidget(QLabel("Représentante:"), 2, 0)
        self.representative = QLineEdit()
        self.representative.setText(self.company_manager.get_field("representative_name"))
        company_layout.addWidget(self.representative, 2, 1)

        company_layout.addWidget(QLabel("Adresse:"), 3, 0)
        self.address = QLineEdit()
        self.address.setText(self.company_manager.get_field("address"))
        company_layout.addWidget(self.address, 3, 1)

        company_layout.addWidget(QLabel("Ville:"), 4, 0)
        self.city = QLineEdit()
        self.city.setText(self.company_manager.get_field("city"))
        company_layout.addWidget(self.city, 4, 1)

        # Save company button
        save_company_btn = QPushButton("Sauvegarder Données Entreprise")
        save_company_btn.setStyleSheet("""
            QPushButton {
                background-color: #D61E8B;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b81970;
            }
        """)
        save_company_btn.clicked.connect(self.save_company_data)
        company_layout.addWidget(save_company_btn, 5, 0, 1, 2)

        company_group.setLayout(company_layout)
        layout.addWidget(company_group)

        # Excel Import Section
        excel_group = QGroupBox("Importer depuis Excel (Optionnel)")
        excel_layout = QGridLayout()

        # Local file row
        excel_layout.addWidget(QLabel("Fichier local:"), 0, 0)
        self.excel_file_path = QLineEdit()
        self.excel_file_path.setReadOnly(True)
        excel_layout.addWidget(self.excel_file_path, 0, 1)

        browse_btn = QPushButton("Parcourir...")
        browse_btn.setMaximumWidth(120)
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #162142;
                color: white;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0d1628;
            }
        """)
        browse_btn.clicked.connect(self.browse_excel_file)
        excel_layout.addWidget(browse_btn, 0, 2)

        # URL row
        excel_layout.addWidget(QLabel("Ou URL cloud:"), 1, 0)
        self.excel_url = QLineEdit()
        self.excel_url.setPlaceholderText("https://drive.google.com/... ou https://dropbox.com/...")
        excel_layout.addWidget(self.excel_url, 1, 1)

        download_btn = QPushButton("Télécharger")
        download_btn.setMaximumWidth(120)
        download_btn.setStyleSheet("""
            QPushButton {
                background-color: #D61E8B;
                color: white;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b81970;
            }
        """)
        download_btn.clicked.connect(self.download_excel_from_url)
        excel_layout.addWidget(download_btn, 1, 2)

        # Employee selection row
        excel_layout.addWidget(QLabel("Sélectionner Employé par ID:"), 2, 0)
        self.employee_id_combo = QComboBox()
        self.employee_id_combo.addItem("-- Aucun --")
        self.employee_id_combo.currentTextChanged.connect(self.on_employee_selected)
        excel_layout.addWidget(self.employee_id_combo, 2, 1, 1, 2)

        excel_group.setLayout(excel_layout)
        layout.addWidget(excel_group)

        # Employee Section
        employee_group = QGroupBox("Données de l'Employé(e)")
        employee_layout = QGridLayout()

        employee_layout.addWidget(QLabel("Nom & Prénom:"), 0, 0)
        self.employee_name = QLineEdit()
        employee_layout.addWidget(self.employee_name, 0, 1)

        employee_layout.addWidget(QLabel("Genre:"), 1, 0)
        self.gender = QComboBox()
        self.gender.addItems(["Madame", "Monsieur"])
        employee_layout.addWidget(self.gender, 1, 1)

        employee_layout.addWidget(QLabel("CIN:"), 2, 0)
        self.cin = QLineEdit()
        employee_layout.addWidget(self.cin, 2, 1)

        employee_layout.addWidget(QLabel("Lieu Délivrance CIN:"), 3, 0)
        self.cin_place = QLineEdit()
        employee_layout.addWidget(self.cin_place, 3, 1)

        employee_layout.addWidget(QLabel("Date CIN:"), 4, 0)
        self.cin_date = QDateEdit()
        self.cin_date.setDate(QDate(2020, 1, 1))
        employee_layout.addWidget(self.cin_date, 4, 1)

        employee_layout.addWidget(QLabel("Poste:"), 5, 0)
        self.position = QLineEdit()
        employee_layout.addWidget(self.position, 5, 1)

        employee_layout.addWidget(QLabel("Date de Début:"), 6, 0)
        self.start_date = QDateEdit()
        self.start_date.setDate(QDate.currentDate())
        employee_layout.addWidget(self.start_date, 6, 1)

        employee_group.setLayout(employee_layout)
        layout.addWidget(employee_group)

        # Document Reference Section
        ref_group = QGroupBox("Numérotation de Document")
        ref_layout = QGridLayout()

        ref_layout.addWidget(QLabel("Année (prefix):"), 0, 0)
        self.year_prefix = QSpinBox()
        self.year_prefix.setValue(self.ref_manager.get_year_prefix())
        self.year_prefix.setRange(0, 99)
        ref_layout.addWidget(self.year_prefix, 0, 1)

        ref_layout.addWidget(QLabel("Compteur:"), 1, 0)
        self.counter = QSpinBox()
        self.counter.setValue(self.ref_manager.get_counter())
        ref_layout.addWidget(self.counter, 1, 1)

        ref_layout.addWidget(QLabel("Prévisualisation:"), 2, 0)
        self.preview_ref = QLineEdit()
        self.preview_ref.setReadOnly(True)
        self.preview_ref.setText(self.ref_manager.get_next_reference())
        ref_layout.addWidget(self.preview_ref, 2, 1)

        reset_btn = QPushButton("Réinitialiser")
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #162142;
                color: white;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0d1628;
            }
        """)
        reset_btn.clicked.connect(self.reset_reference)
        ref_layout.addWidget(reset_btn, 3, 0, 1, 2)

        ref_group.setLayout(ref_layout)
        layout.addWidget(ref_group)

        # Buttons Section
        button_layout = QHBoxLayout()

        generate_btn = QPushButton("Générer Attestation")
        generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #D61E8B;
                color: white;
                padding: 12px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #b81970;
            }
        """)
        generate_btn.clicked.connect(self.generate_attestation)
        button_layout.addWidget(generate_btn)

        layout.addLayout(button_layout)

        # Progress
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        layout.addStretch()
        self.setLayout(layout)

        # Connect signals
        self.year_prefix.valueChanged.connect(self.update_preview)
        self.counter.valueChanged.connect(self.update_preview)

    def update_preview(self):
        """Update reference preview"""
        ref = f"{self.year_prefix.value():02d}/{self.counter.value():04d}"
        self.preview_ref.setText(ref)

    def save_company_data(self):
        """Save company data to persistent storage"""
        try:
            self.company_manager.set_company_data(
                name=self.company_name.text(),
                legal_id=self.legal_id.text(),
                representative=self.representative.text(),
                address=self.address.text(),
                city=self.city.text()
            )
            QMessageBox.information(
                self, "Succès",
                "Données de l'entreprise sauvegardées avec succès!"
            )
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Erreur lors de la sauvegarde: {str(e)}")

    def reset_reference(self):
        """Reset reference counter"""
        reply = QMessageBox.question(
            self, "Confirmation",
            "Êtes-vous sûr de vouloir réinitialiser le compteur?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.ref_manager.reset_counter()
            self.counter.setValue(0)
            self.update_preview()

    def browse_excel_file(self):
        """Browse and load Excel file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner Fichier Excel", "", "Excel Files (*.xlsx *.xls)"
        )
        if not file_path:
            return

        importer = ExcelImporter()
        if importer.load_excel(file_path):
            self.excel_file_path.setText(file_path)
            self.excel_rows = importer.get_all_rows()
            
            # Index data by ID if ID column exists
            self.excel_data = {}
            id_column = None
            
            # Find ID column
            for col in importer.get_columns():
                if col.lower() in ['id', 'employee_id', 'cin']:
                    id_column = col
                    break
            
            if id_column:
                for row in self.excel_rows:
                    emp_id = str(row.get(id_column, ''))
                    if emp_id:
                        self.excel_data[emp_id] = row
                
                # Update combobox with employee IDs
                self.employee_id_combo.blockSignals(True)
                self.employee_id_combo.clear()
                self.employee_id_combo.addItem("-- Aucun --")
                self.employee_id_combo.addItems(sorted(self.excel_data.keys()))
                self.employee_id_combo.blockSignals(False)
                
                QMessageBox.information(
                    self, "Succès",
                    f"Fichier Excel chargé avec {len(self.excel_data)} employés"
                )
            else:
                QMessageBox.warning(
                    self, "Avertissement",
                    "Aucune colonne 'ID' trouvée. Le fichier doit contenir une colonne 'ID'"
                )
        else:
            QMessageBox.critical(self, "Erreur", "Impossible de charger le fichier Excel")

    def download_excel_from_url(self):
        """Download Excel file from URL and load it"""
        raw_url = self.excel_url.text().strip()
        if not raw_url:
            QMessageBox.warning(self, "Avertissement", "Veuillez entrer une URL valide")
            return

        url = normalize_cloud_excel_url(raw_url)
        if url != raw_url:
            self.excel_url.setText(url)

        temp_file_path = None
        try:
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)
            QApplication.processEvents()

            try:
                temp_file_path = download_to_temp_excel(url)
            except Exception as e:
                if 'sharepoint.com' in url or '1drv.ms' in url or 'onedrive.live.com' in url:
                    alt_url = url
                    if 'download=1' not in url:
                        alt_url = url + ('&' if '?' in url else '?') + 'download=1'
                    if alt_url != url:
                        temp_file_path = download_to_temp_excel(alt_url)
                        self.excel_url.setText(alt_url)
                    else:
                        raise
                else:
                    raise

            # temp_file_path is already set by download_to_temp_excel
            if not temp_file_path or not os.path.exists(temp_file_path):
                raise FileNotFoundError("Fichier Excel téléchargé introuvable après téléchargement.")

            importer = ExcelImporter()
            if importer.load_excel(temp_file_path):
                self.excel_file_path.setText(url)
                self.excel_rows = importer.get_all_rows()

                self.excel_data = {}
                id_column = None
                for col in importer.get_columns():
                    if col.lower() in ['id', 'employee_id', 'cin']:
                        id_column = col
                        break

                if id_column:
                    for row in self.excel_rows:
                        emp_id = str(row.get(id_column, ''))
                        if emp_id:
                            self.excel_data[emp_id] = row

                    self.employee_id_combo.blockSignals(True)
                    self.employee_id_combo.clear()
                    self.employee_id_combo.addItem("-- Aucun --")
                    self.employee_id_combo.addItems(sorted(self.excel_data.keys()))
                    self.employee_id_combo.blockSignals(False)

                    QMessageBox.information(
                        self, "Succès",
                        f"Fichier Excel téléchargé et chargé avec {len(self.excel_data)} employés"
                    )
                else:
                    QMessageBox.warning(
                        self, "Avertissement",
                        "Aucune colonne 'ID' trouvée. Le fichier doit contenir une colonne 'ID'"
                    )
            else:
                QMessageBox.critical(self, "Erreur", "Impossible de charger le fichier Excel téléchargé")

            try:
                if temp_file_path:
                    os.unlink(temp_file_path)
            except Exception:
                pass

        except requests.exceptions.RequestException as e:
            QMessageBox.critical(self, "Erreur de Téléchargement", f"Impossible de télécharger le fichier: {str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Erreur lors du traitement: {str(e)}")
        finally:
            self.progress.setVisible(False)

    def on_employee_selected(self, emp_id: str):
        """Handle employee selection from dropdown"""
        if emp_id == "-- Aucun --" or emp_id not in self.excel_data:
            return
        
        row_data = self.excel_data[emp_id]
        self.fill_from_row(row_data)

    def load_from_excel(self):
        """Legacy method - kept for compatibility"""
        self.browse_excel_file()

    def show_excel_selection(self, importer: ExcelImporter, rows: list):
        """Legacy method - kept for compatibility"""
        pass

    def fill_from_row(self, row_data: dict):
        """Fill form fields from Excel row"""
        # Map common Excel column names to form fields
        field_mapping = {
            'employee_name': self.employee_name,
            'nom & prénom': self.employee_name,
            'nom': self.employee_name,
            'name': self.employee_name,
            'gender': self.gender,
            'genre': self.gender,
            'cin': self.cin,
            'cin_place': self.cin_place,
            'lieu délivrance cin': self.cin_place,
            'position': self.position,
            'poste': self.position,
        }

        for key, value in row_data.items():
            key_lower = key.lower()
            if key_lower in field_mapping:
                widget = field_mapping[key_lower]
                
                # Handle different widget types
                if hasattr(widget, 'setText'):
                    widget.setText(str(value) if value else '')
                elif hasattr(widget, 'setCurrentText'):
                    widget.setCurrentText(str(value) if value else '')

    def validate_inputs(self) -> bool:
        """Validate all required fields"""
        if not self.employee_name.text():
            QMessageBox.warning(self, "Validation", "Veuillez entrer le nom de l'employé")
            return False
        if not self.cin.text():
            QMessageBox.warning(self, "Validation", "Veuillez entrer le CIN")
            return False
        if not self.position.text():
            QMessageBox.warning(self, "Validation", "Veuillez entrer le poste")
            return False
        return True

    def generate_attestation(self):
        """Generate Attestation de Travail"""
        if not self.validate_inputs():
            return

        try:
            # Create models
            employee = Employee(
                name=self.employee_name.text(),
                gender=self.gender.currentText(),
                cin=self.cin.text(),
                cin_place=self.cin_place.text() or "Tunis",
                cin_date=self.cin_date.date().toPyDate(),
                position=self.position.text(),
                start_date=self.start_date.date().toPyDate()
            )

            company = Company(
                name=self.company_name.text(),
                legal_id=self.legal_id.text(),
                representative_name=self.representative.text(),
                address=self.address.text(),
                city=self.city.text()
            )

            # Generate reference
            self.ref_manager.set_year_prefix(self.year_prefix.value())
            self.ref_manager.set_counter(self.counter.value())
            reference = self.ref_manager.generate_reference()
            self.counter.setValue(self.ref_manager.get_counter())

            config = DocumentConfig(
                employee=employee,
                company=company,
                reference=reference,
                document_type="attestation"
            )

            # Generate suggested filename
            safe_name = self.employee_name.text().replace(" ", "_").replace("/", "_")
            suggested_filename = f"Attestation_{safe_name}_{reference.replace('/', '-')}.docx"

            # Show save dialog
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Sauvegarder Attestation de Travail",
                suggested_filename,
                "Word Documents (*.docx);;All Files (*)"
            )

            if not file_path:
                # User cancelled
                return

            # Generate document and save to selected location
            handler = TemplateHandler()
            result = handler.create_attestation_document(config, file_path)

            # Verify file was saved
            result_path = Path(result)
            if result_path.exists():
                # Add to history
                self.history_manager.add_document(
                    reference=reference,
                    doc_type="attestation",
                    employee_name=employee.name,
                    file_path=result_path
                )
                
                QMessageBox.information(
                    self, "Succès",
                    f"Attestation sauvegardée avec succès!\n\nFichier: {result}"
                )
            else:
                QMessageBox.critical(
                    self, "Erreur",
                    f"Le fichier n'a pas pu être sauvegardé à:\n{result}"
                )
        except Exception as e:
            QMessageBox.critical(self, "Erreur Génération", f"Erreur lors de la génération:\n{str(e)}")


class OrdreTab(QWidget):
    """Tab for Ordre de Mission generation"""

    def __init__(self, ref_manager: ReferenceManager, history_manager: DocumentHistory):
        super().__init__()
        self.ref_manager = ref_manager
        self.history_manager = history_manager
        self.company_manager = CompanyManager()
        self.excel_data = {}  # Store loaded Excel data indexed by ID
        self.excel_rows = []  # Store all rows for reference
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Define common style for input fields
        input_style = """
            QLineEdit, QDateEdit, QComboBox, QSpinBox {
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 8px;
                background-color: white;
                color: #162142;
                font-size: 11px;
            }
            QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 2px solid #D61E8B;
                background-color: #fff9fc;
            }
            QDateEdit::down-button, QComboBox::down-button {
                border: none;
                background-color: #D61E8B;
                width: 20px;
            }
        """
        self.setStyleSheet(input_style)

        # Employee Section
        employee_group = QGroupBox("Données de l'Employé(e)")
        employee_layout = QGridLayout()

        employee_layout.addWidget(QLabel("Nom & Prénom:"), 0, 0)
        self.employee_name = QLineEdit()
        employee_layout.addWidget(self.employee_name, 0, 1)

        employee_layout.addWidget(QLabel("Nationalité:"), 1, 0)
        self.nationality = QLineEdit()
        employee_layout.addWidget(self.nationality, 1, 1)

        employee_layout.addWidget(QLabel("Date de Naissance:"), 2, 0)
        self.birth_date = QDateEdit()
        self.birth_date.setDate(QDate(1990, 1, 1))
        employee_layout.addWidget(self.birth_date, 2, 1)

        employee_layout.addWidget(QLabel("Lieu de Naissance:"), 3, 0)
        self.birth_place = QLineEdit()
        employee_layout.addWidget(self.birth_place, 3, 1)

        employee_layout.addWidget(QLabel("CIN:"), 4, 0)
        self.cin = QLineEdit()
        employee_layout.addWidget(self.cin, 4, 1)

        employee_layout.addWidget(QLabel("Adresse:"), 5, 0)
        self.address = QLineEdit()
        employee_layout.addWidget(self.address, 5, 1)

        employee_layout.addWidget(QLabel("Genre:"), 6, 0)
        self.gender = QComboBox()
        self.gender.addItems(["Madame", "Monsieur"])
        employee_layout.addWidget(self.gender, 6, 1)

        employee_layout.addWidget(QLabel("Poste:"), 7, 0)
        self.position = QLineEdit()
        employee_layout.addWidget(self.position, 7, 1)

        employee_group.setLayout(employee_layout)
        layout.addWidget(employee_group)

        # Excel Import Section
        excel_group = QGroupBox("Importer depuis Excel (Optionnel)")
        excel_layout = QGridLayout()

        # Local file row
        excel_layout.addWidget(QLabel("Fichier local:"), 0, 0)
        self.excel_file_path = QLineEdit()
        self.excel_file_path.setReadOnly(True)
        excel_layout.addWidget(self.excel_file_path, 0, 1)

        browse_btn = QPushButton("Parcourir...")
        browse_btn.setMaximumWidth(120)
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #162142;
                color: white;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0d1628;
            }
        """)
        browse_btn.clicked.connect(self.browse_excel_file)
        excel_layout.addWidget(browse_btn, 0, 2)

        # URL row
        excel_layout.addWidget(QLabel("Ou URL cloud:"), 1, 0)
        self.excel_url = QLineEdit()
        self.excel_url.setPlaceholderText("https://drive.google.com/... ou https://dropbox.com/...")
        excel_layout.addWidget(self.excel_url, 1, 1)

        download_btn = QPushButton("Télécharger")
        download_btn.setMaximumWidth(120)
        download_btn.setStyleSheet("""
            QPushButton {
                background-color: #D61E8B;
                color: white;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b81970;
            }
        """)
        download_btn.clicked.connect(self.download_excel_from_url)
        excel_layout.addWidget(download_btn, 1, 2)

        # Employee selection row
        excel_layout.addWidget(QLabel("Sélectionner Employé par ID:"), 2, 0)
        self.employee_id_combo = QComboBox()
        self.employee_id_combo.addItem("-- Aucun --")
        self.employee_id_combo.currentTextChanged.connect(self.on_employee_selected)
        excel_layout.addWidget(self.employee_id_combo, 2, 1, 1, 2)

        excel_group.setLayout(excel_layout)
        layout.addWidget(excel_group)

        # Mission Details Section
        mission_group = QGroupBox("Détails de la Mission")
        mission_layout = QGridLayout()

        mission_layout.addWidget(QLabel("Destination/Pays:"), 0, 0)
        self.country = QComboBox()
        self.country.addItems(["Allemagne", "France"])
        mission_layout.addWidget(self.country, 0, 1)

        mission_layout.addWidget(QLabel("Date Début Mission:"), 1, 0)
        self.mission_start_date = QDateEdit()
        self.mission_start_date.setDate(QDate.currentDate())
        mission_layout.addWidget(self.mission_start_date, 1, 1)

        mission_layout.addWidget(QLabel("Date Fin Mission:"), 2, 0)
        self.mission_end_date = QDateEdit()
        self.mission_end_date.setDate(QDate.currentDate().addDays(7))
        mission_layout.addWidget(self.mission_end_date, 2, 1)

        mission_group.setLayout(mission_layout)
        layout.addWidget(mission_group)

        # Reference
        ref_group = QGroupBox("Numérotation")
        ref_layout = QHBoxLayout()
        ref_layout.addWidget(QLabel("Référence:"))
        self.preview_ref = QLineEdit()
        self.preview_ref.setReadOnly(True)
        self.preview_ref.setText(self.ref_manager.get_next_reference())
        ref_layout.addWidget(self.preview_ref)
        ref_group.setLayout(ref_layout)
        layout.addWidget(ref_group)

        # Buttons
        button_layout = QHBoxLayout()

        generate_btn = QPushButton("Générer Ordre de Mission")
        generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #D61E8B;
                color: white;
                padding: 12px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #b81970;
            }
        """)
        generate_btn.clicked.connect(self.generate_ordre)
        button_layout.addWidget(generate_btn)
        
        layout.addLayout(button_layout)

        # Progress
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        layout.addStretch()
        self.setLayout(layout)

    def browse_excel_file(self):
        """Browse and load Excel file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner Fichier Excel", "", "Excel Files (*.xlsx *.xls)"
        )
        if not file_path:
            return

        importer = ExcelImporter()
        if importer.load_excel(file_path):
            self.excel_file_path.setText(file_path)
            self.excel_rows = importer.get_all_rows()
            
            # Index data by ID if ID column exists
            self.excel_data = {}
            id_column = None
            
            # Find ID column
            for col in importer.get_columns():
                if col.lower() in ['id', 'employee_id', 'cin']:
                    id_column = col
                    break
            
            if id_column:
                for row in self.excel_rows:
                    emp_id = str(row.get(id_column, ''))
                    if emp_id:
                        self.excel_data[emp_id] = row
                
                # Update combobox with employee IDs
                self.employee_id_combo.blockSignals(True)
                self.employee_id_combo.clear()
                self.employee_id_combo.addItem("-- Aucun --")
                self.employee_id_combo.addItems(sorted(self.excel_data.keys()))
                self.employee_id_combo.blockSignals(False)
                
                QMessageBox.information(
                    self, "Succès",
                    f"Fichier Excel chargé avec {len(self.excel_data)} employés"
                )
            else:
                QMessageBox.warning(
                    self, "Avertissement",
                    "Aucune colonne 'ID' trouvée. Le fichier doit contenir une colonne 'ID'"
                )
        else:
            QMessageBox.critical(self, "Erreur", "Impossible de charger le fichier Excel")

    def download_excel_from_url(self):
        """Download Excel file from URL and load it"""
        raw_url = self.excel_url.text().strip()
        if not raw_url:
            QMessageBox.warning(self, "Avertissement", "Veuillez entrer une URL valide")
            return

        url = normalize_cloud_excel_url(raw_url)
        if url != raw_url:
            self.excel_url.setText(url)

        temp_file_path = None
        try:
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)
            QApplication.processEvents()

            try:
                temp_file_path = download_to_temp_excel(url)
            except Exception as e:
                if 'sharepoint.com' in url or '1drv.ms' in url or 'onedrive.live.com' in url:
                    alt_url = url
                    if 'download=1' not in url:
                        alt_url = url + ('&' if '?' in url else '?') + 'download=1'
                    if alt_url != url:
                        temp_file_path = download_to_temp_excel(alt_url)
                        self.excel_url.setText(alt_url)
                    else:
                        raise
                else:
                    raise

            # temp_file_path is already set by download_to_temp_excel
            if not temp_file_path or not os.path.exists(temp_file_path):
                raise FileNotFoundError("Fichier Excel téléchargé introuvable après téléchargement.")

            importer = ExcelImporter()
            if importer.load_excel(temp_file_path):
                self.excel_file_path.setText(url)
                self.excel_rows = importer.get_all_rows()

                self.excel_data = {}
                id_column = None
                for col in importer.get_columns():
                    if col.lower() in ['id', 'employee_id', 'cin']:
                        id_column = col
                        break

                if id_column:
                    for row in self.excel_rows:
                        emp_id = str(row.get(id_column, ''))
                        if emp_id:
                            self.excel_data[emp_id] = row

                    self.employee_id_combo.blockSignals(True)
                    self.employee_id_combo.clear()
                    self.employee_id_combo.addItem("-- Aucun --")
                    self.employee_id_combo.addItems(sorted(self.excel_data.keys()))
                    self.employee_id_combo.blockSignals(False)

                    QMessageBox.information(
                        self, "Succès",
                        f"Fichier Excel téléchargé et chargé avec {len(self.excel_data)} employés"
                    )
                else:
                    QMessageBox.warning(
                        self, "Avertissement",
                        "Aucune colonne 'ID' trouvée. Le fichier doit contenir une colonne 'ID'"
                    )
            else:
                QMessageBox.critical(self, "Erreur", "Impossible de charger le fichier Excel téléchargé")

            try:
                if temp_file_path:
                    os.unlink(temp_file_path)
            except Exception:
                pass

        except requests.exceptions.RequestException as e:
            QMessageBox.critical(self, "Erreur de Téléchargement", f"Impossible de télécharger le fichier: {str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Erreur lors du traitement: {str(e)}")
        finally:
            self.progress.setVisible(False)

    def on_employee_selected(self, emp_id: str):
        """Handle employee selection from dropdown"""
        if emp_id == "-- Aucun --" or emp_id not in self.excel_data:
            return
        
        row_data = self.excel_data[emp_id]
        self.fill_from_row(row_data)

    def load_from_excel(self):
        """Legacy method - kept for compatibility"""
        self.browse_excel_file()

    def show_excel_selection(self, importer: ExcelImporter, rows: list):
        """Legacy method - kept for compatibility"""
        pass

    def fill_from_row(self, row_data: dict):
        """Fill form fields from Excel row"""
        field_mapping = {
            'employee_name': self.employee_name,
            'nom & prénom': self.employee_name,
            'nom': self.employee_name,
            'name': self.employee_name,
            'nationality': self.nationality,
            'nationalité': self.nationality,
            'birth_date': self.birth_date,
            'date naissance': self.birth_date,
            'date_naissance': self.birth_date,
            'birth_place': self.birth_place,
            'lieu naissance': self.birth_place,
            'lieu_naissance': self.birth_place,
            'cin': self.cin,
            'address': self.address,
            'adresse': self.address,
            'position': self.position,
            'poste': self.position,
        }

        for key, value in row_data.items():
            key_lower = key.lower()
            if key_lower in field_mapping:
                widget = field_mapping[key_lower]
                
                # Handle different widget types
                if hasattr(widget, 'setText'):
                    widget.setText(str(value) if value else '')
                elif hasattr(widget, 'setCurrentText'):
                    widget.setCurrentText(str(value) if value else '')
                elif hasattr(widget, 'setDate'):
                    # Try to parse date
                    try:
                        from datetime import datetime
                        # Try different date formats
                        for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y']:
                            try:
                                dt = datetime.strptime(str(value), fmt)
                                widget.setDate(QDate(dt.year, dt.month, dt.day))
                                break
                            except:
                                continue
                    except:
                        pass

    def generate_ordre(self):
        """Generate Ordre de Mission"""
        if not self.employee_name.text() or not self.position.text():
            QMessageBox.warning(self, "Validation", "Veuillez remplir tous les champs obligatoires")
            return

        try:
            employee = Employee(
                name=self.employee_name.text(),
                gender=self.gender.currentText(),
                cin=self.cin.text(),
                cin_place=self.birth_place.text() or "Tunis",
                cin_date=self.birth_date.date().toPyDate(),
                position=self.position.text(),
                start_date=self.mission_start_date.date().toPyDate()
            )

            company = Company()

            reference = self.ref_manager.generate_reference()

            config = DocumentConfig(
                employee=employee,
                company=company,
                reference=reference,
                document_type="ordre_mission"
            )

            # Generate suggested filename
            safe_name = self.employee_name.text().replace(" ", "_").replace("/", "_")
            suggested_filename = f"Ordre_{safe_name}_{reference.replace('/', '-')}.docx"

            # Show save dialog
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Sauvegarder Ordre de Mission",
                suggested_filename,
                "Word Documents (*.docx);;All Files (*)"
            )

            if not file_path:
                # User cancelled
                return

            # Generate document and save to selected location
            handler = TemplateHandler()
            result = handler.create_ordre_mission_document(
                config, 
                file_path,
                country=self.country.currentText(),
                nationality=self.nationality.text(),
                address=self.address.text(),
                mission_end_date=self.mission_end_date.date().toPyDate()
            )

            # Verify file was saved
            result_path = Path(result)
            if result_path.exists():
                # Add to history
                self.history_manager.add_document(
                    reference=reference,
                    doc_type="ordre_mission",
                    employee_name=employee.name,
                    file_path=result_path
                )
                
                QMessageBox.information(
                    self, "Succès",
                    f"Ordre de Mission sauvegardé avec succès!\n\nFichier: {result}"
                )
                self.preview_ref.setText(self.ref_manager.get_next_reference())
            else:
                QMessageBox.critical(
                    self, "Erreur",
                    f"Le fichier n'a pas pu être sauvegardé à:\n{result}"
                )
        except Exception as e:
            QMessageBox.critical(self, "Erreur Génération", f"Erreur lors de la génération:\n{str(e)}")


class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self):
        super().__init__()
        self.ref_manager = ReferenceManager()
        self.history_manager = DocumentHistory()
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("HR Automated Docs - PLW Tunisia")
        self.setGeometry(100, 100, 1200, 900)
        self.setMinimumSize(900, 700)

        # Set application style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QTabWidget::pane {
                border: 1px solid #e0e0e0;
            }
            QTabBar::tab {
                background-color: #e8e8e8;
                color: #162142;
                padding: 8px 20px;
                margin-right: 2px;
                border: 1px solid #d0d0d0;
                border-bottom: none;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #162142;
                color: white;
                padding: 8px 20px;
                margin-right: 2px;
                border: 1px solid #162142;
                border-bottom: none;
            }
            QGroupBox {
                color: #162142;
                border: 2px solid #D61E8B;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QLabel {
                color: #162142;
                font-weight: 500;
            }
            QPushButton {
                padding: 10px 20px;
                border-radius: 5px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                opacity: 0.9;
            }
        """)

        # Central widget
        central = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Scroll helper button (small screens)
        scroll_down_btn = QPushButton("Défiler vers le bas")
        scroll_down_btn.setMaximumWidth(160)
        scroll_down_btn.setStyleSheet("""
            QPushButton {
                background-color: #162142;
                color: white;
                border-radius: 5px;
                padding: 4px 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0d1628;
            }
        """)
        scroll_down_btn.clicked.connect(lambda: self.scroll_relative(200))
        main_layout.addWidget(scroll_down_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # Header section with logo and title
        header_layout = QVBoxLayout()
        header_layout.setSpacing(10)
        header_layout.setContentsMargins(0, 10, 0, 10)

        # Logo - Centered at top
        logo_container = QHBoxLayout()
        logo_label = QLabel()
        logo_path = Path("templates/logo.png")
        if logo_path.exists():
            pixmap = QIcon(str(logo_path)).pixmap(120, 120)
            logo_label.setPixmap(pixmap)
            logo_label.setMaximumSize(140, 140)
            logo_label.setStyleSheet("""
                QLabel {
                    background-color: white;
                    border: 2px solid #D61E8B;
                    border-radius: 10px;
                    padding: 5px;
                }
            """)
        logo_container.addStretch()
        logo_container.addWidget(logo_label)
        logo_container.addStretch()
        header_layout.addLayout(logo_container)

        # Title - Centered
        title_layout = QHBoxLayout()
        title_layout.setSpacing(10)
        
        title_vbox = QVBoxLayout()
        main_title = QLabel("HR Automated Docs")
        main_title.setFont(QFont("Arial", 26, QFont.Weight.Bold))
        main_title.setStyleSheet("color: #162142;")
        main_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        subtitle = QLabel("PLW Tunisia")
        subtitle.setFont(QFont("Arial", 13))
        subtitle.setStyleSheet("color: #D61E8B; font-weight: bold;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        title_vbox.addWidget(main_title)
        title_vbox.addWidget(subtitle)
        
        title_layout.addStretch()
        title_layout.addLayout(title_vbox)
        title_layout.addStretch()
        
        header_layout.addLayout(title_layout)
        
        main_layout.addLayout(header_layout)

        # Separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("border: 2px solid #D61E8B;")
        main_layout.addWidget(separator)

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(AttestationTab(self.ref_manager, self.history_manager), "Attestation de Travail")
        tabs.addTab(OrdreTab(self.ref_manager, self.history_manager), "Ordre de Mission")
        tabs.addTab(HistoryTab(self.history_manager), "Historique")
        main_layout.addWidget(tabs)

        central.setLayout(main_layout)

        # Make the whole window scrollable for full-screen/minimized devices
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.setCentralWidget(scroll)

    def scroll_relative(self, delta_y: int):
        """Scroll the central area by a fixed number of pixels."""
        scroll = self.centralWidget()
        if isinstance(scroll, QScrollArea):
            current = scroll.verticalScrollBar().value()
            scroll.verticalScrollBar().setValue(current + delta_y)


class HistoryTab(QWidget):
    """Tab for viewing document generation history"""

    def __init__(self, history_manager: DocumentHistory):
        super().__init__()
        self.history_manager = history_manager
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Title
        title = QLabel("Historique des Documents Générés")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #162142; margin-bottom: 10px;")
        layout.addWidget(title)

        # Controls
        controls_layout = QHBoxLayout()

        # Filter by type
        filter_label = QLabel("Filtrer par type:")
        controls_layout.addWidget(filter_label)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Tous", "Attestation de Travail", "Ordre de Mission"])
        self.filter_combo.currentTextChanged.connect(self.refresh_table)
        controls_layout.addWidget(self.filter_combo)

        controls_layout.addStretch()

        # Refresh button
        refresh_btn = QPushButton("Actualiser")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #162142;
                color: white;
                padding: 8px 15px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0d1628;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_table)
        controls_layout.addWidget(refresh_btn)

        # Clear history button
        clear_btn = QPushButton("Effacer Historique")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                padding: 8px 15px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        clear_btn.clicked.connect(self.clear_history)
        controls_layout.addWidget(clear_btn)

        layout.addLayout(controls_layout)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Date & Heure", "Référence", "Type de Document", "Employé(e)", "Chemin du Fichier"
        ])

        # Set column widths
        self.table.setColumnWidth(0, 150)  # Date & Time
        self.table.setColumnWidth(1, 120)  # Reference
        self.table.setColumnWidth(2, 180)  # Document Type
        self.table.setColumnWidth(3, 200)  # Employee Name
        self.table.setColumnWidth(4, 300)  # File Path

        # Table styling
        self.table.setStyleSheet("""
            QTableWidget {
                gridline-color: #d0d0d0;
                selection-background-color: #D61E8B;
            }
            QHeaderView::section {
                background-color: #162142;
                color: white;
                padding: 8px;
                border: none;
                font-weight: bold;
            }
        """)

        # Make table read-only
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.table)

        # Statistics
        stats_group = QGroupBox("Statistiques")
        stats_layout = QHBoxLayout()

        self.total_docs_label = QLabel("Total: 0 documents")
        self.attestation_count_label = QLabel("Attestations: 0")
        self.ordre_count_label = QLabel("Ordres de Mission: 0")

        stats_layout.addWidget(self.total_docs_label)
        stats_layout.addWidget(self.attestation_count_label)
        stats_layout.addWidget(self.ordre_count_label)
        stats_layout.addStretch()

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        self.setLayout(layout)

        # Load initial data
        self.refresh_table()

    def refresh_table(self):
        """Refresh the table with current data"""
        filter_type = self.filter_combo.currentText()

        if filter_type == "Tous":
            documents = self.history_manager.get_all_documents()
        elif filter_type == "Attestation de Travail":
            documents = self.history_manager.get_documents_by_type("attestation")
        else:  # Ordre de Mission
            documents = self.history_manager.get_documents_by_type("ordre_mission")

        # Sort by timestamp (most recent first)
        documents.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        self.table.setRowCount(len(documents))

        for row, doc in enumerate(documents):
            # Date & Time
            date_item = QTableWidgetItem(doc.get('date', 'N/A'))
            self.table.setItem(row, 0, date_item)

            # Reference
            ref_item = QTableWidgetItem(doc.get('reference', 'N/A'))
            self.table.setItem(row, 1, ref_item)

            # Document Type
            doc_type = doc.get('doc_type', 'N/A')
            if doc_type == 'attestation':
                display_type = 'Attestation de Travail'
            elif doc_type == 'ordre_mission':
                display_type = 'Ordre de Mission'
            else:
                display_type = doc_type
            type_item = QTableWidgetItem(display_type)
            self.table.setItem(row, 2, type_item)

            # Employee Name
            employee_item = QTableWidgetItem(doc.get('employee_name', 'N/A'))
            self.table.setItem(row, 3, employee_item)

            # File Path
            path_item = QTableWidgetItem(doc.get('file_path', 'N/A'))
            self.table.setItem(row, 4, path_item)

        # Update statistics
        self.update_statistics()

    def update_statistics(self):
        """Update the statistics display"""
        all_docs = self.history_manager.get_all_documents()
        attestations = self.history_manager.get_documents_by_type("attestation")
        ordres = self.history_manager.get_documents_by_type("ordre_mission")

        self.total_docs_label.setText(f"Total: {len(all_docs)} documents")
        self.attestation_count_label.setText(f"Attestations: {len(attestations)}")
        self.ordre_count_label.setText(f"Ordres de Mission: {len(ordres)}")

    def clear_history(self):
        """Clear all document history"""
        reply = QMessageBox.question(
            self, "Confirmation",
            "Êtes-vous sûr de vouloir effacer tout l'historique des documents?\n\nCette action est irréversible.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.history_manager.clear_history()
            self.refresh_table()
            QMessageBox.information(
                self, "Succès",
                "L'historique des documents a été effacé."
            )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
