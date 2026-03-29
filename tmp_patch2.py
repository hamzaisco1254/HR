from pathlib import Path
p = Path('src/ui/main_window.py')
text = p.read_text(encoding='utf-8')
start = text.find('def download_excel_from_url(self):')
if start == -1:
    raise SystemExit('method not found')
# find after first method text to modify block
block_start = text.find('        temp_file_path = None', start)
if block_start == -1:
    raise SystemExit('block not found')
block_end = text.find('            content_length = response.headers.get(\'content-length\')', block_start)
if block_end == -1:
    raise SystemExit('block end not found')
# extend to include response.raise_for_status line
block_end = text.find('            response.raise_for_status()', block_start) + len('            response.raise_for_status()\n')
old_block = text[block_start:block_end]
new_block = '''        temp_file_path = None
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
text = text[:block_start] + new_block + text[block_end:]
# Save
p.write_text(text, encoding='utf-8')
print('updated first download block')
