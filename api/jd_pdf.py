"""
PDF renderer for generated job descriptions.

ReportLab-based. Self-contained: no external assets, no native deps.
Output is an A4 portrait document with the company gradient header,
section titles in the Planisware pink, and clean Helvetica body text.
"""
import io
from datetime import datetime
from typing import Dict, List

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem,
    Table, TableStyle, PageBreak, KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# Brand colors (mirror the web app)
COLOR_PINK    = colors.HexColor('#ec4899')
COLOR_PURPLE  = colors.HexColor('#7c3aed')
COLOR_NAVY    = colors.HexColor('#0f1628')
COLOR_TEXT    = colors.HexColor('#111827')
COLOR_MUTED   = colors.HexColor('#6b7280')
COLOR_BORDER  = colors.HexColor('#e5e7eb')
COLOR_BG_HDR  = colors.HexColor('#f9fafb')


def _styles():
    base = getSampleStyleSheet()
    return {
        'title':        ParagraphStyle('JDTitle',  parent=base['Title'],
                                       fontName='Helvetica-Bold',
                                       fontSize=22, leading=26, textColor=colors.white,
                                       alignment=TA_LEFT, spaceAfter=2),
        'subtitle':     ParagraphStyle('JDSubtitle', parent=base['Normal'],
                                       fontName='Helvetica',
                                       fontSize=10, leading=13, textColor=colors.HexColor('#fce7f3'),
                                       alignment=TA_LEFT, spaceAfter=0),
        'section_hdr':  ParagraphStyle('JDSection', parent=base['Heading2'],
                                       fontName='Helvetica-Bold',
                                       fontSize=13, leading=16, textColor=COLOR_PINK,
                                       spaceBefore=14, spaceAfter=8,
                                       borderPadding=0),
        'paragraph':    ParagraphStyle('JDParagraph', parent=base['BodyText'],
                                       fontName='Helvetica', fontSize=10.5, leading=15,
                                       textColor=COLOR_TEXT, spaceAfter=8, alignment=TA_LEFT),
        'bullet':       ParagraphStyle('JDBullet', parent=base['BodyText'],
                                       fontName='Helvetica', fontSize=10.5, leading=15,
                                       textColor=COLOR_TEXT, leftIndent=0, spaceAfter=2),
        'meta_label':   ParagraphStyle('JDMetaLabel', parent=base['Normal'],
                                       fontName='Helvetica', fontSize=8.5,
                                       textColor=COLOR_MUTED, leading=11),
        'meta_value':   ParagraphStyle('JDMetaValue', parent=base['Normal'],
                                       fontName='Helvetica-Bold', fontSize=10,
                                       textColor=COLOR_TEXT, leading=12),
        'footer':       ParagraphStyle('JDFooter', parent=base['Normal'],
                                       fontName='Helvetica', fontSize=8,
                                       textColor=COLOR_MUTED, alignment=TA_RIGHT),
    }


def _escape(text: str) -> str:
    """Light HTML escape for ReportLab Paragraph (& only — < > are mostly fine in JD text)."""
    if text is None:
        return ''
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))


def _header_table(jd: Dict, styles) -> Table:
    """Gradient-styled colored band with title + role chips."""
    title = _escape(jd.get('title') or 'Fiche de poste')

    # Role metadata as a sub-line
    parts = []
    if jd.get('seniority'):     parts.append(jd['seniority'].capitalize())
    if jd.get('contract_type'): parts.append(jd['contract_type'])
    if jd.get('location'):      parts.append(jd['location'])
    if jd.get('department_name'): parts.append(f"Departement : {jd['department_name']}")
    subtitle = '  ·  '.join(_escape(p) for p in parts) or 'Fiche de poste'

    inner = [
        [Paragraph(title, styles['title'])],
        [Paragraph(subtitle, styles['subtitle'])],
    ]
    inner_tbl = Table(inner, colWidths=[170 * mm])
    inner_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_PINK),
        ('LEFTPADDING', (0, 0), (-1, -1), 18),
        ('RIGHTPADDING', (0, 0), (-1, -1), 18),
        ('TOPPADDING',   (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 12),
    ]))
    return inner_tbl


def _section(label: str, content_flowable, styles) -> List:
    """A section: title + content."""
    return [
        Paragraph(_escape(label), styles['section_hdr']),
        content_flowable,
    ]


def _bullet_list(items: List[str], styles) -> ListFlowable:
    if not items:
        return Paragraph('<i>(aucun element)</i>', styles['paragraph'])
    return ListFlowable(
        [
            ListItem(Paragraph(_escape(it), styles['bullet']),
                     leftIndent=10, bulletColor=COLOR_PURPLE,
                     bulletFontName='Helvetica-Bold')
            for it in items
        ],
        bulletType='bullet',
        start='•',
        bulletColor=COLOR_PURPLE,
        leftIndent=14,
        spaceAfter=4,
    )


def render_jd_pdf(jd: Dict) -> bytes:
    """Render a saved JD draft (full row from generated_jds) to PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=(jd.get('title') or 'Fiche de poste'),
        author='Planisware HR',
    )
    s = _styles()
    content = jd.get('content') or {}

    story: List = []

    # Header band
    story.append(_header_table(jd, s))
    story.append(Spacer(1, 14))

    # Summary
    if content.get('summary'):
        story += _section('Resume', Paragraph(_escape(content['summary']), s['paragraph']), s)

    # Mission
    if content.get('mission'):
        story += _section('Mission principale', Paragraph(_escape(content['mission']), s['paragraph']), s)

    # Responsibilities
    if content.get('responsibilities'):
        story += _section('Responsabilites cles', _bullet_list(content['responsibilities'], s), s)

    # Profile
    if content.get('requirements'):
        story += _section('Profil recherche', _bullet_list(content['requirements'], s), s)

    # Skills required
    if content.get('skills_required'):
        story += _section('Competences requises', _bullet_list(content['skills_required'], s), s)

    # Skills preferred
    if content.get('skills_preferred'):
        story += _section('Competences appreciees', _bullet_list(content['skills_preferred'], s), s)

    # Benefits
    if content.get('benefits'):
        story += _section('Conditions et avantages', _bullet_list(content['benefits'], s), s)

    # Process
    if content.get('process'):
        story += _section('Processus de recrutement', Paragraph(_escape(content['process']), s['paragraph']), s)

    # Page footer with timestamp
    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(COLOR_MUTED)
        canvas.drawRightString(
            A4[0] - 20 * mm, 10 * mm,
            f"Planisware HR · genere le {datetime.utcnow().strftime('%Y-%m-%d')} · page {doc_.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def safe_filename(title: str) -> str:
    """Slug-style file name for the download."""
    import re
    base = (title or 'fiche-de-poste').strip().lower()
    base = re.sub(r'[^a-z0-9]+', '-', base).strip('-')
    return (base or 'fiche-de-poste')[:80] + '.pdf'
