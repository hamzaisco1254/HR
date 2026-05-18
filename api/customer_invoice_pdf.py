"""
Customer invoice PDF renderer — Tunisian VAT-compliant.

Branded A4 invoice with the canonical sections expected by a Tunisian
client/accountant: header with sequential ref + dates, vendor/client
block, HT/TVA/TTC breakdown, payment terms, optional signature line.

Uses ReportLab (same pattern as jd_pdf.py).
"""
import io
import re
from datetime import datetime
from typing import Dict, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)


COLOR_PINK   = colors.HexColor('#ec4899')
COLOR_PURPLE = colors.HexColor('#7c3aed')
COLOR_NAVY   = colors.HexColor('#0f1628')
COLOR_TEXT   = colors.HexColor('#111827')
COLOR_MUTED  = colors.HexColor('#6b7280')
COLOR_BORDER = colors.HexColor('#d1d5db')
COLOR_BG     = colors.HexColor('#f9fafb')


def _money(v) -> float:
    try: return float(v or 0)
    except (TypeError, ValueError): return 0.0


def _fmt_amount(v: float, currency: str) -> str:
    return f"{v:,.2f}".replace(',', ' ').replace('.', ',') + ' ' + (currency or 'EUR')


def _escape(text) -> str:
    if text is None: return ''
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))


def _styles():
    base = getSampleStyleSheet()
    return {
        'company_name': ParagraphStyle('CompanyName', parent=base['Title'],
                                       fontName='Helvetica-Bold', fontSize=18, leading=22,
                                       textColor=COLOR_NAVY, alignment=TA_LEFT, spaceAfter=2),
        'company_meta': ParagraphStyle('CompanyMeta', parent=base['Normal'],
                                       fontName='Helvetica', fontSize=9, leading=12,
                                       textColor=COLOR_MUTED, alignment=TA_LEFT),
        'invoice_title': ParagraphStyle('InvoiceTitle', parent=base['Title'],
                                        fontName='Helvetica-Bold', fontSize=28, leading=34,
                                        textColor=COLOR_PINK, alignment=TA_RIGHT, spaceAfter=0),
        'invoice_meta': ParagraphStyle('InvoiceMeta', parent=base['Normal'],
                                       fontName='Helvetica', fontSize=10, leading=14,
                                       textColor=COLOR_TEXT, alignment=TA_RIGHT),
        'h_section':   ParagraphStyle('HSection', parent=base['Heading4'],
                                      fontName='Helvetica-Bold', fontSize=9, leading=12,
                                      textColor=COLOR_MUTED, alignment=TA_LEFT,
                                      spaceBefore=4, spaceAfter=4),
        'normal':      ParagraphStyle('NormalCustom', parent=base['Normal'],
                                      fontName='Helvetica', fontSize=10, leading=14,
                                      textColor=COLOR_TEXT),
        'normal_bold': ParagraphStyle('NormalBold', parent=base['Normal'],
                                      fontName='Helvetica-Bold', fontSize=10, leading=14,
                                      textColor=COLOR_TEXT),
        'right':       ParagraphStyle('Right', parent=base['Normal'],
                                      fontName='Helvetica', fontSize=10, leading=14,
                                      textColor=COLOR_TEXT, alignment=TA_RIGHT),
        'right_bold':  ParagraphStyle('RightBold', parent=base['Normal'],
                                      fontName='Helvetica-Bold', fontSize=11, leading=14,
                                      textColor=COLOR_TEXT, alignment=TA_RIGHT),
        'total':       ParagraphStyle('Total', parent=base['Normal'],
                                      fontName='Helvetica-Bold', fontSize=14, leading=18,
                                      textColor='#fff', alignment=TA_RIGHT),
        'footer':      ParagraphStyle('Footer', parent=base['Normal'],
                                      fontName='Helvetica', fontSize=8, leading=10,
                                      textColor=COLOR_MUTED, alignment=TA_CENTER),
    }


def _header_table(invoice: Dict, company: Dict, s):
    """Top band: company on the left, big FACTURE label on the right."""
    left = [
        [Paragraph(_escape(company.get('name') or 'Planisware HR'), s['company_name'])],
        [Paragraph(_escape(company.get('legal_id') or ''), s['company_meta'])],
        [Paragraph(_escape(company.get('address') or ''), s['company_meta'])],
        [Paragraph(_escape(company.get('city') or ''), s['company_meta'])],
        [Paragraph(_escape(company.get('representative_name') or ''), s['company_meta'])],
    ]
    left_tbl = Table(left, colWidths=[100 * mm])
    left_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
    ]))

    right = [
        [Paragraph("FACTURE", s['invoice_title'])],
        [Paragraph(_escape("N° " + (invoice.get('invoice_ref') or '—')), s['invoice_meta'])],
        [Paragraph(_escape("Date d'émission : " + (invoice.get('issue_date') or '')), s['invoice_meta'])],
        [Paragraph(_escape("Date d'échéance : " + (invoice.get('due_date') or '—')), s['invoice_meta'])],
    ]
    right_tbl = Table(right, colWidths=[65 * mm])
    right_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
    ]))

    container = Table([[left_tbl, right_tbl]], colWidths=[105 * mm, 65 * mm])
    container.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return container


def _parties_block(invoice: Dict, client: Dict, s):
    """Client info block."""
    parts = [
        [Paragraph("FACTURÉ À", s['h_section'])],
        [Paragraph(_escape(client.get('name') or invoice.get('client_name') or ''), s['normal_bold'])],
    ]
    if client.get('legal_name'):
        parts.append([Paragraph(_escape(client['legal_name']), s['normal'])])
    if client.get('address'):
        parts.append([Paragraph(_escape(client['address']), s['normal'])])
    if client.get('vat_id'):
        parts.append([Paragraph(_escape(f"N° TVA : {client['vat_id']}"), s['normal'])])
    if client.get('country'):
        parts.append([Paragraph(_escape(f"Pays : {client['country']}"), s['normal'])])

    tbl = Table(parts, colWidths=[170 * mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, -1), COLOR_BG),
        ('LEFTPADDING',  (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('TOPPADDING',   (0, 0), (0, 0), 12),
        ('BOTTOMPADDING',(0, -1), (-1, -1), 12),
        ('BOX',          (0, 0), (-1, -1), 0.5, COLOR_BORDER),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
    ]))
    return tbl


def _lines_table(invoice: Dict, s):
    """Single-line table (the platform handles one invoice = one billing event)."""
    description = invoice.get('description') or 'Prestations de services'
    if invoice.get('project_name'):
        description += f"\n— Projet : {invoice['project_name']}"
    if invoice.get('period_month'):
        description += f"\n— Période : {invoice['period_month']}"
    if invoice.get('effort_days'):
        description += f"\n— Effort estimé : {invoice['effort_days']} jours"

    currency = invoice.get('currency') or 'EUR'
    amount_ht = _money(invoice.get('amount_ht'))
    vat_rate  = _money(invoice.get('vat_rate'))
    vat_amt   = _money(invoice.get('vat_amount'))
    amount_ttc = amount_ht + vat_amt
    # If HT not set, fall back to amount as TTC and compute HT
    if amount_ht == 0:
        amt = _money(invoice.get('amount'))
        if vat_rate > 0:
            amount_ht = amt / (1 + vat_rate / 100.0)
            vat_amt   = amt - amount_ht
        else:
            amount_ht = amt
            vat_amt   = 0
        amount_ttc = amt

    header = ['Description', 'Quantité', 'P.U. HT', 'Total HT']
    body = [[
        Paragraph(_escape(description).replace('\n', '<br/>'), s['normal']),
        Paragraph('1', s['right']),
        Paragraph(_fmt_amount(amount_ht, currency), s['right']),
        Paragraph(_fmt_amount(amount_ht, currency), s['right']),
    ]]
    tbl = Table([header] + body, colWidths=[85 * mm, 20 * mm, 30 * mm, 35 * mm])
    tbl.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_NAVY),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, 0), 9),
        ('ALIGN',      (1, 0), (-1, 0), 'RIGHT'),
        # Body
        ('VALIGN',     (0, 1), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING',   (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 8),
        ('LINEABOVE',  (0, 1), (-1, 1), 0.3, COLOR_BORDER),
        ('LINEBELOW',  (0, -1), (-1, -1), 0.3, COLOR_BORDER),
    ]))
    return tbl, amount_ht, vat_rate, vat_amt, amount_ttc


def _totals_table(currency: str, amount_ht: float, vat_rate: float,
                  vat_amt: float, amount_ttc: float, s):
    rows = [
        ['Sous-total HT',     _fmt_amount(amount_ht, currency)],
        [f"TVA ({vat_rate:.2f}%)", _fmt_amount(vat_amt, currency)],
        ['Total TTC',         _fmt_amount(amount_ttc, currency)],
    ]
    para_rows = []
    for label, value in rows:
        is_total = (label.startswith('Total'))
        if is_total:
            para_rows.append([
                Paragraph(label, s['total']),
                Paragraph(value, s['total']),
            ])
        else:
            para_rows.append([
                Paragraph(label, s['right']),
                Paragraph(value, s['right_bold']),
            ])
    tbl = Table(para_rows, colWidths=[100 * mm, 70 * mm])
    tbl.setStyle(TableStyle([
        ('LINEABOVE',     (0, 0), (-1, 0), 0.3, COLOR_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING',   (0, 0), (-1, -1), 10),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
        # Highlight TTC row
        ('BACKGROUND',    (0, 2), (-1, 2), COLOR_PINK),
        ('TOPPADDING',    (0, 2), (-1, 2), 10),
        ('BOTTOMPADDING', (0, 2), (-1, 2), 10),
    ]))
    return tbl


def _payment_block(invoice: Dict, company: Dict, s):
    notes = []
    notes.append("Modalités de paiement : à régler dans le délai indiqué ci-dessus.")
    if (invoice.get('currency') or '').upper() != 'TND' and invoice.get('amount_tnd'):
        try:
            tnd = float(invoice.get('amount_tnd') or 0)
            if tnd > 0:
                notes.append(f"Équivalent en TND (au taux du jour de l'émission) : {tnd:,.2f}".replace(',', ' ').replace('.', ',') + " TND")
        except (TypeError, ValueError):
            pass
    bank = company.get('bank_details') or ''
    if bank:
        notes.append("Coordonnées bancaires : " + bank)
    notes.append("En cas de retard de paiement, des pénalités au taux légal applicable pourront être appliquées.")

    rows = [[Paragraph("CONDITIONS DE PAIEMENT", s['h_section'])]]
    for n in notes:
        rows.append([Paragraph(_escape(n), s['normal'])])

    tbl = Table(rows, colWidths=[170 * mm])
    tbl.setStyle(TableStyle([
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
    ]))
    return tbl


def render_invoice_pdf(invoice: Dict, client: Dict, company: Dict) -> bytes:
    """Render a customer invoice draft to PDF bytes.

    Inputs are plain dicts; the caller is responsible for marshaling
    the customer_invoice row + client row + company config.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Facture {invoice.get('invoice_ref') or ''}",
        author=(company.get('name') or 'Planisware HR'),
    )
    s = _styles()
    story = []

    story.append(_header_table(invoice, company, s))
    story.append(Spacer(1, 14))
    story.append(_parties_block(invoice, client, s))
    story.append(Spacer(1, 14))

    lines_tbl, amount_ht, vat_rate, vat_amt, amount_ttc = _lines_table(invoice, s)
    story.append(lines_tbl)
    story.append(Spacer(1, 8))

    currency = invoice.get('currency') or 'EUR'
    story.append(_totals_table(currency, amount_ht, vat_rate, vat_amt, amount_ttc, s))
    story.append(Spacer(1, 14))

    story.append(_payment_block(invoice, company, s))

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(COLOR_MUTED)
        footer_text = (company.get('name') or 'Planisware HR')
        if company.get('legal_id'):
            footer_text += " · " + company['legal_id']
        canvas.drawCentredString(A4[0] / 2.0, 10 * mm,
                                 footer_text + f" · page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def safe_filename(invoice_ref: str, fallback: str = 'facture') -> str:
    base = (invoice_ref or fallback).strip().lower()
    base = re.sub(r'[^a-z0-9]+', '-', base).strip('-')
    return (base or fallback) + '.pdf'
