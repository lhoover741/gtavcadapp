import secrets
import logging
from datetime import datetime, timedelta
from database import db
from community_service import get_current_community_id, scoped_query
from models import CaseFile, Arrest, Warrant, Civilian

logger = logging.getLogger(__name__)

CASE_STATUSES = ['Open', 'Pending Trial', 'In Trial', 'Awaiting Verdict', 'Closed', 'Dismissed', 'Appealed']
COURT_OUTCOMES = ['Guilty', 'Not Guilty', 'Plea Deal', 'Mistrial', 'Dismissed', 'Acquitted']
SENTENCING_OPTIONS = ['Probation', 'Prison', 'Fine', 'Community Service', 'Parole', 'Suspended']

def create_case_from_arrest(arrest_id, civilian_id, charges):
    """Create a case file from an arrest."""
    case_id = f"CASE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()

    case = CaseFile(
        community_id=get_current_community_id(),
        case_id=case_id,
        defendant_civilian_id=civilian_id,
        charges=charges,
        arrest_id=arrest_id,
        status='Open',
    )

    try:
        db.session.add(case)
        db.session.commit()
        return case
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create case: {e}')
        raise

def assign_judge(case_id, judge_name):
    """Assign a judge to a case."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case:
        return None

    case.assigned_judge = judge_name
    case.status = 'Pending Trial'
    case.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return case
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to assign judge: {e}')
        raise

def add_prosecutor_notes(case_id, notes):
    """Add prosecutor notes to case."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case:
        return None

    entry = f"\n[{datetime.now().isoformat()}] Prosecutor: {notes}"
    if case.prosecutor_notes:
        case.prosecutor_notes += entry
    else:
        case.prosecutor_notes = entry

    case.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return case
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to add prosecutor notes: {e}')
        raise

def add_defense_notes(case_id, notes):
    """Add defense notes to case."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case:
        return None

    entry = f"\n[{datetime.now().isoformat()}] Defense: {notes}"
    if case.defense_notes:
        case.defense_notes += entry
    else:
        case.defense_notes = entry

    case.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return case
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to add defense notes: {e}')
        raise

def set_court_date(case_id, court_date):
    """Set court date for case."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case:
        return None

    case.court_date = court_date
    case.status = 'Pending Trial'
    case.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return case
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to set court date: {e}')
        raise

def close_case(case_id, outcome, sentence_type, sentence_length, notes):
    """Close a case with verdict and sentencing."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case:
        return None

    case.status = 'Closed'
    case.outcome = f"Verdict: {outcome}\nSentence: {sentence_type} - {sentence_length}\nNotes: {notes}"
    case.updated_at = datetime.utcnow()

    # Update civilian record if guilty
    if outcome == 'Guilty':
        civilian = scoped_query(Civilian).filter_by(civilian_id=case.defendant_civilian_id).first()
        if civilian:
            # Add to criminal history
            entry = f"[{datetime.now().strftime('%Y-%m-%d')}] Case {case_id}: {outcome} - {sentence_type}"
            if civilian.criminal_background:
                civilian.criminal_background += f"\n{entry}"
            else:
                civilian.criminal_background = entry

            # Update parole/probation status
            if sentence_type == 'Probation':
                civilian.probation_status = 'Active'
            elif sentence_type == 'Parole':
                civilian.parole_status = 'Active'

            civilian.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return case
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to close case: {e}')
        raise

def get_case_summary(case_id):
    """Get complete case summary."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case:
        return None

    civilian = scoped_query(Civilian).filter_by(civilian_id=case.defendant_civilian_id).first()

    return {
        'case_id': case.case_id,
        'defendant': civilian.full_name if civilian else 'Unknown',
        'charges': case.charges,
        'status': case.status,
        'assigned_judge': case.assigned_judge,
        'court_date': case.court_date.isoformat() if case.court_date else None,
        'prosecutor_notes': case.prosecutor_notes,
        'defense_notes': case.defense_notes,
        'outcome': case.outcome,
        'created_at': case.created_at.isoformat() if case.created_at else None,
    }

def search_cases(query):
    """Search cases by defendant name or case ID."""
    cases = scoped_query(CaseFile).filter(
        (CaseFile.case_id.ilike(f'%{query}%')) |
        (CaseFile.defendant_civilian_id.ilike(f'%{query}%'))
    ).limit(50).all()

    result = []
    for case in cases:
        civilian = scoped_query(Civilian).filter_by(civilian_id=case.defendant_civilian_id).first()
        result.append({
            'case_id': case.case_id,
            'defendant': civilian.full_name if civilian else 'Unknown',
            'charges': case.charges,
            'status': case.status,
            'created_at': case.created_at.isoformat() if case.created_at else None,
        })

    return result
