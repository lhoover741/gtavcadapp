from datetime import datetime
from html import escape
from io import BytesIO
import re

from werkzeug.utils import secure_filename

WARRANT_TYPES = (
    'Arrest Warrant',
    'Search Warrant',
    'Bench Warrant',
    'Administrative Warrant',
    'Extradition Warrant',
    'Fugitive Warrant',
    'Alias Warrant',
)

TYPE_PREFIXES = {
    'Arrest Warrant': 'ARW',
    'Search Warrant': 'SRW',
    'Bench Warrant': 'BNW',
    'Administrative Warrant': 'ADW',
    'Extradition Warrant': 'EXW',
    'Fugitive Warrant': 'FGW',
    'Alias Warrant': 'ALW',
}

TYPE_SPECIFIC_FIELDS = {
    'Search Warrant': (('Search Location', 'search_location'), ('Items to Seize', 'items_to_seize')),
    'Bench Warrant': (('Court Case Number', 'court_case_number'), ('Failure Reason', 'bench_failure_reason')),
    'Administrative Warrant': (('Administrative Basis', 'administrative_basis'), ('Inspection Scope', 'inspection_scope')),
    'Extradition Warrant': (('Originating Jurisdiction', 'originating_jurisdiction'), ('Extradition Location', 'extradition_location')),
    'Fugitive Warrant': (('Last Known Location', 'fugitive_last_known_location'),),
    'Alias Warrant': (('Alias Names', 'alias_names'),),
}

DISCLAIMER = 'Roleplay CAD document generated for GTAVCAD community use.'


def safe_warrant_pdf_filename(warrant_number, warrant_type):
    number = secure_filename(str(warrant_number or 'warrant')).strip('._') or 'warrant'
    type_slug = secure_filename(re.sub(r'\s+', '_', str(warrant_type or 'Warrant'))).strip('._') or 'Warrant'
    return f'{number}_{type_slug}.pdf'


def _value(warrant, attr, fallback=''):
    value = getattr(warrant, attr, None)
    if value in (None, '') and fallback:
        value = getattr(warrant, fallback, None)
    return value or ''


def _fmt_dt(value):
    if not value:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M UTC')
    return str(value)


def _pdf_text(value):
    text = str(value or '—')
    text = escape(text, quote=True)
    return text.replace('\n', '<br/>')


def build_warrant_pdf(warrant, *, community_name='', cad_name='', created_by='', approved_by=''):
    """Return a generated warrant PDF as bytes without reading any stored binary input."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError('ReportLab is required to generate warrant PDFs') from exc

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=0.65 * inch, leftMargin=0.65 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='SmallMuted', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#555555')))
    story = []

    warrant_type = _value(warrant, 'warrant_type') or 'Arrest Warrant'
    warrant_number = _value(warrant, 'warrant_number', 'warrant_id')
    story.append(Paragraph(_pdf_text('GTAVCAD Warrant'), styles['Title']))
    story.append(Paragraph(_pdf_text(f'{community_name or "Community"} • {cad_name or "CAD"}'), styles['Heading3']))
    story.append(Spacer(1, 0.15 * inch))

    rows = [
        ('Warrant Number', warrant_number),
        ('Warrant Type', warrant_type),
        ('Status', _value(warrant, 'status', 'warrant_status') or 'Active'),
        ('Subject / Target', _value(warrant, 'subject_name', 'warrant_name')),
        ('Subject DOB', _value(warrant, 'subject_dob')),
        ('Subject Address', _value(warrant, 'subject_address')),
        ('Issuing Agency', _value(warrant, 'issuing_agency', 'warrant_issuer')),
        ('Judge / Authority', _value(warrant, 'judge_or_authority')),
        ('Charges / Basis', _value(warrant, 'charges_or_basis', 'warrant_charges')),
        ('Probable Cause', _value(warrant, 'probable_cause', 'justification') or _value(warrant, 'warrant_notes')),
    ]
    for label, attr in TYPE_SPECIFIC_FIELDS.get(warrant_type, ()):
        rows.append((label, _value(warrant, attr)))
    rows.extend([
        ('Execution Instructions', _value(warrant, 'execution_instructions')),
        ('Issued Date/Time', _fmt_dt(getattr(warrant, 'created_at', None))),
        ('Expiration Date', _value(warrant, 'expiration_date')),
        ('Created By', created_by),
        ('Approved By', approved_by),
    ])
    table_data = [
        [
            Paragraph(f'<b>{escape(str(label), quote=True)}</b>', styles['Normal']),
            Paragraph(_pdf_text(value), styles['Normal'])
        ]
        for label, value in rows
    ]
    table = Table(table_data, colWidths=[1.85 * inch, 5.0 * inch])
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cccccc')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f2f2f2')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(_pdf_text(DISCLAIMER), styles['SmallMuted']))
    doc.build(story)
    return buffer.getvalue()
