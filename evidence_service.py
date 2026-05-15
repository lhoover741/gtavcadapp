import secrets
import logging
from datetime import datetime
from database import db
from community_service import get_current_community_id, scoped_query
from models import Evidence, Arrest, CaseFile

logger = logging.getLogger(__name__)

EVIDENCE_TYPES = [
    'Firearm', 'Ammunition', 'Drugs', 'Cash', 'Stolen Property',
    'Weapon', 'Clothing', 'Vehicle', 'Document', 'Electronics',
    'Jewelry', 'Contraband', 'Biological', 'Chemical', 'Other'
]

def create_evidence(case_id, arrest_id, evidence_type, description, collected_by, location_found):
    """Create evidence record with chain of custody."""
    evidence_id = f"EV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    barcode = f"BAR{secrets.token_hex(4).upper()}"

    evidence = Evidence(
        community_id=get_current_community_id(),
        evidence_id=evidence_id,
        case_number=case_id,
        evidence_description=description,
        collected_by=collected_by,
        location_found=location_found,
        status='In Storage',
        notes=f"Barcode: {barcode}\nCollected: {datetime.now().isoformat()}",
    )

    try:
        db.session.add(evidence)
        db.session.commit()
        return evidence
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create evidence: {e}')
        raise

def transfer_evidence_custody(evidence_id, from_officer, to_officer, reason):
    """Transfer evidence custody with chain of custody log."""
    evidence = scoped_query(Evidence).filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return None

    transfer_entry = f"\n[{datetime.now().isoformat()}] Transfer: {from_officer} → {to_officer} ({reason})"
    if evidence.notes:
        evidence.notes += transfer_entry
    else:
        evidence.notes = transfer_entry

    evidence.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return evidence
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to transfer evidence: {e}')
        raise

def get_evidence_chain_of_custody(evidence_id):
    """Get complete chain of custody for evidence."""
    evidence = scoped_query(Evidence).filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return None

    # Parse chain of custody from notes
    chain = []
    if evidence.notes:
        for line in evidence.notes.split('\n'):
            if 'Transfer:' in line or 'Collected:' in line:
                chain.append(line.strip())

    return {
        'evidence_id': evidence.evidence_id,
        'description': evidence.evidence_description,
        'type': evidence.evidence_description.split()[0] if evidence.evidence_description else 'Unknown',
        'collected_by': evidence.collected_by,
        'location_found': evidence.location_found,
        'status': evidence.status,
        'collected_date': evidence.created_at.isoformat() if evidence.created_at else None,
        'chain_of_custody': chain,
    }

def link_evidence_to_case(evidence_id, case_id):
    """Link evidence to a case."""
    evidence = scoped_query(Evidence).filter_by(evidence_id=evidence_id).first()
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()

    if not evidence or not case:
        return None

    evidence.case_number = case_id
    evidence.updated_at = datetime.utcnow()

    # Update case evidence list
    if case.evidence_ids:
        if evidence_id not in case.evidence_ids:
            case.evidence_ids += f",{evidence_id}"
    else:
        case.evidence_ids = evidence_id

    case.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return evidence
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to link evidence: {e}')
        raise

def get_case_evidence(case_id):
    """Get all evidence for a case."""
    case = scoped_query(CaseFile).filter_by(case_id=case_id).first()
    if not case or not case.evidence_ids:
        return []

    evidence_ids = case.evidence_ids.split(',')
    evidence_list = []

    for eid in evidence_ids:
        ev = scoped_query(Evidence).filter_by(evidence_id=eid.strip()).first()
        if ev:
            evidence_list.append({
                'evidence_id': ev.evidence_id,
                'description': ev.evidence_description,
                'collected_by': ev.collected_by,
                'status': ev.status,
            })

    return evidence_list

def release_evidence(evidence_id, reason):
    """Release evidence from storage."""
    evidence = scoped_query(Evidence).filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return None

    evidence.status = 'Released'
    release_entry = f"\n[{datetime.now().isoformat()}] Released: {reason}"
    if evidence.notes:
        evidence.notes += release_entry
    else:
        evidence.notes = release_entry

    evidence.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return evidence
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to release evidence: {e}')
        raise

def destroy_evidence(evidence_id, reason):
    """Destroy evidence after case closure."""
    evidence = scoped_query(Evidence).filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return None

    evidence.status = 'Destroyed'
    destroy_entry = f"\n[{datetime.now().isoformat()}] Destroyed: {reason}"
    if evidence.notes:
        evidence.notes += destroy_entry
    else:
        evidence.notes = destroy_entry

    evidence.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return evidence
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to destroy evidence: {e}')
        raise
