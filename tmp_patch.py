from pathlib import Path
path = Path('src/ui/main_window.py')
text = path.read_text(encoding='utf-8')
old = '''    def download_excel_from_url(self):
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

            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
'''
new = '''    def download_excel_from_url(self):
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
'''
count = text.count(old)
print('found', count)
if count == 0:
    raise SystemExit('snippet not found')
text = text.replace(old, new)
path.write_text(text, encoding='utf-8')
print('done')
