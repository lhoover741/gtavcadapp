import secrets
import logging
from datetime import datetime
from database import db
from community_service import scoped_query
from models import Arrest, Warrant, Bolo, Evidence

logger = logging.getLogger(__name__)

CHARGE_CATEGORIES = [
    'Violent Crime', 'Property Crime', 'Drug Offense', 'Traffic Violation',
    'Fraud', 'Theft', 'Assault', 'Homicide', 'Robbery', 'Burglary',
    'DUI', 'Weapons Violation', 'Gang Activity', 'Prostitution',
    'Trespassing', 'Vandalism', 'Disorderly Conduct', 'Resisting Arrest'
]

GANG_AFFILIATIONS = [
    'Grove Street Families', 'Ballas', 'Vagos', 'Mafia', 'Triads',
    'Bikers', 'Street Crew', 'Cartel', 'Syndicate', 'Independent'
]


def get_civilian_charges(civilian_id):
    """Get all charges for a civilian by matching suspect_name via civilian lookup."""
    from models import Civilian
    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return []

    name = civilian.full_name or f"{civilian.first_name or ''} {civilian.last_name or ''}".strip()
    arrests = Arrest.query.filter(Arrest.suspect_name.ilike(f'%{name}%')).all() if name else []

    charges = []
    for arrest in arrests:
        if arrest.charges:
            charge_list = arrest.charges.split(',') if isinstance(arrest.charges, str) else arrest.charges
            for charge in charge_list:
                charges.append({
                    'arrest_id': arrest.arrest_id,
                    'charge': charge.strip(),
                    'date': arrest.created_at.isoformat() if arrest.created_at else None,
                    'status': arrest.status,
                })

    return charges


def get_civilian_warrants(civilian_id):
    """Get all active warrants for a civilian by matching warrant_name."""
    from models import Civilian
    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return []

    name = civilian.full_name or f"{civilian.first_name or ''} {civilian.last_name or ''}".strip()
    warrants = Warrant.query.filter(
        Warrant.warrant_name.ilike(f'%{name}%'),
        Warrant.warrant_status == 'Active'
    ).all() if name else []

    result = []
    for warrant in warrants:
        result.append({
            'warrant_id': warrant.warrant_id,
            'charges': warrant.warrant_charges,
            'issued_by': warrant.warrant_issuer,
            'issued_date': warrant.created_at.isoformat() if warrant.created_at else None,
            'status': warrant.warrant_status,
        })

    return result


def get_civilian_bolos(civilian_id):
    """Get all active BOLOs for a civilian by matching suspect_name."""
    from models import Civilian
    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return []

    name = civilian.full_name or f"{civilian.first_name or ''} {civilian.last_name or ''}".strip()
    bolos = Bolo.query.filter(
        Bolo.suspect_name.ilike(f'%{name}%'),
        Bolo.status == 'Active'
    ).all() if name else []

    result = []
    for bolo in bolos:
        result.append({
            'bolo_id': bolo.bolo_id,
            'description': bolo.description,
            'threat_level': bolo.threat_level,
            'issued_by': bolo.issued_by,
            'issued_date': bolo.created_at.isoformat() if bolo.created_at else None,
            'status': bolo.status,
        })

    return result


def search_bolos(query):
    """Search BOLO archive."""
    bolos = Bolo.query.filter(
        (Bolo.description.ilike(f'%{query}%')) |
        (Bolo.suspect_name.ilike(f'%{query}%'))
    ).limit(50).all()

    result = []
    for bolo in bolos:
        result.append({
            'bolo_id': bolo.bolo_id,
            'suspect_name': bolo.suspect_name,
            'description': bolo.description,
            'threat_level': bolo.threat_level,
            'status': bolo.status,
            'issued_date': bolo.created_at.isoformat() if bolo.created_at else None,
        })

    return result


def search_warrants(query):
    """Search warrant archive."""
    warrants = Warrant.query.filter(
        (Warrant.warrant_charges.ilike(f'%{query}%')) |
        (Warrant.warrant_name.ilike(f'%{query}%'))
    ).limit(50).all()

    result = []
    for warrant in warrants:
        result.append({
            'warrant_id': warrant.warrant_id,
            'warrant_name': warrant.warrant_name,
            'charges': warrant.warrant_charges,
            'status': warrant.warrant_status,
            'issued_date': warrant.created_at.isoformat() if warrant.created_at else None,
        })

    return result


def get_evidence_chain_of_custody(evidence_id):
    """Get chain of custody for evidence."""
    evidence = Evidence.query.filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return None

    return {
        'evidence_id': evidence.evidence_id,
        'case_number': evidence.case_number,
        'description': evidence.evidence_description,
        'collected_by': evidence.collected_by,
        'location_found': evidence.location_found,
        'status': evidence.status,
        'collected_date': evidence.created_at.isoformat() if evidence.created_at else None,
        'notes': evidence.notes,
    }


def transfer_evidence(evidence_id, from_officer, to_officer, reason):
    """Transfer evidence custody."""
    evidence = Evidence.query.filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return None

    transfer_note = f"Transfer from {from_officer} to {to_officer}: {reason}"
    if evidence.notes:
        evidence.notes = f"{evidence.notes}\n{transfer_note}"
    else:
        evidence.notes = transfer_note

    evidence.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return evidence
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to transfer evidence: {e}')
        raise


def get_gang_members(gang_name):
    """Get all known members of a gang."""
    from models import Civilian

    members = scoped_query(Civilian).filter_by(gang_affiliation=gang_name).all()

    result = []
    for member in members:
        result.append({
            'civilian_id': member.civilian_id,
            'name': member.full_name,
            'gang_rank': member.gang_rank or 'Member',
            'risk_level': member.risk_level,
            'warrant_risk': member.warrant_risk,
        })

    return result


def get_gang_statistics():
    """Get statistics on gang affiliations."""
    from models import Civilian

    gangs = {}
    civilians = Civilian.query.filter(
        Civilian.gang_affiliation != None,
        Civilian.gang_affiliation != '',
        Civilian.gang_affiliation != 'None'
    ).all()

    for civ in civilians:
        gang = civ.gang_affiliation or 'Unknown'
        if gang not in gangs:
            gangs[gang] = {'count': 0, 'high_risk': 0}
        gangs[gang]['count'] += 1
        if civ.risk_level in ['High', 'Critical']:
            gangs[gang]['high_risk'] += 1

    return gangs


def archive_bolo(bolo_id, resolution):
    """Archive a BOLO."""
    bolo = scoped_query(Bolo).filter_by(bolo_id=bolo_id).first()
    if not bolo:
        return None

    bolo.status = 'Archived'
    # Store resolution in the charges field as a note since Bolo has no notes column
    bolo.charges = f"{bolo.charges or ''}\n[Archived: {resolution}]".strip()
    bolo.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return bolo
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to archive BOLO: {e}')
        raise


def archive_warrant(warrant_id, resolution):
    """Archive a warrant."""
    warrant = scoped_query(Warrant).filter_by(warrant_id=warrant_id).first()
    if not warrant:
        return None

    warrant.warrant_status = 'Archived'
    warrant.warrant_notes = f"{warrant.warrant_notes or ''}\n[Archived: {resolution}]".strip()
    warrant.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return warrant
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to archive warrant: {e}')
        raise
