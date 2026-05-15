import secrets
import logging
from datetime import datetime
from database import db
from community_service import get_current_community_id, scoped_query
from models import Civilian, Vehicle, Warrant, Arrest, Bolo, KnownAssociate

logger = logging.getLogger(__name__)


def link_civilian_to_vehicle(civilian_id, vehicle_id):
    """Link a civilian as owner of a vehicle."""
    vehicle = scoped_query(Vehicle).filter_by(vehicle_id=vehicle_id).first()
    if not vehicle:
        return None

    vehicle.owner_civilian_id = civilian_id
    vehicle.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return vehicle
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to link vehicle: {e}')
        raise


def create_known_associate(civilian_id, associated_id, relationship_type):
    """Create a relationship between two civilians."""
    associate_id = f"ASSOC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    associate = KnownAssociate(
        community_id=get_current_community_id(),
        associate_id=associate_id,
        civilian_id=civilian_id,
        associated_civilian_id=associated_id,
        relationship_type=relationship_type,
    )

    try:
        db.session.add(associate)
        db.session.commit()
        return associate
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create association: {e}')
        raise


def get_gang_crew(gang_name):
    """Get all members of a gang as a connected crew."""
    members = scoped_query(Civilian).filter_by(gang_affiliation=gang_name).all()

    crew = []
    for member in members:
        associates = scoped_query(KnownAssociate).filter_by(civilian_id=member.civilian_id).all()

        crew.append({
            'civilian_id': member.civilian_id,
            'name': member.full_name,
            'rank': member.gang_rank or 'Member',
            'risk_level': member.risk_level,
            'known_associates': [a.associated_civilian_id for a in associates],
            'vehicles': [v.vehicle_id for v in scoped_query(Vehicle).filter_by(owner_civilian_id=member.civilian_id).all()],
        })

    return crew


def create_arrest_record(civilian_id, charges, arresting_officer, location, narrative):
    """Create arrest and update civilian criminal history."""
    arrest_id = f"ARR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()

    arrest = Arrest(
        community_id=get_current_community_id(),
        arrest_id=arrest_id,
        civilian_id=civilian_id,
        suspect_name=civilian.full_name if civilian else None,
        charges=charges,
        arresting_officer=arresting_officer,
        arrest_location=location,
        narrative=narrative,
        status='Active',
    )

    # Update civilian criminal background
    if civilian:
        entry = f"[{datetime.now().strftime('%Y-%m-%d')}] {charges}"
        if civilian.criminal_background:
            civilian.criminal_background = f"{civilian.criminal_background}\n{entry}"
        else:
            civilian.criminal_background = entry
        civilian.updated_at = datetime.utcnow()

    try:
        db.session.add(arrest)
        db.session.commit()
        return arrest
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create arrest: {e}')
        raise


def create_warrant_from_arrest(arrest_id, civilian_id, charges, probable_cause):
    """Create warrant from arrest."""
    warrant_id = f"WAR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()

    warrant = Warrant(
        community_id=get_current_community_id(),
        warrant_id=warrant_id,
        civilian_id=civilian_id,
        warrant_name=civilian.full_name if civilian else 'Unknown',
        warrant_charges=charges,
        warrant_issuer='Court',
        warrant_status='Active',
        warrant_notes=f"Probable Cause: {probable_cause}",
    )

    # Update civilian warrant risk
    if civilian:
        civilian.warrant_risk = 'High'
        civilian.updated_at = datetime.utcnow()

    try:
        db.session.add(warrant)
        db.session.commit()
        return warrant
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create warrant: {e}')
        raise


def check_warrant_on_traffic_stop(plate):
    """Check if vehicle owner has active warrants."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle or not vehicle.owner_civilian_id:
        return None

    warrants = Warrant.query.filter(
        (Warrant.civilian_id == vehicle.owner_civilian_id) &
        (Warrant.warrant_status == 'Active')
    ).all()

    return {
        'vehicle': vehicle.vehicle_id,
        'owner': vehicle.owner_civilian_id,
        'warrants': [w.warrant_id for w in warrants],
        'warrant_count': len(warrants),
    }


def get_civilian_criminal_history(civilian_id):
    """Get complete criminal history for a civilian."""
    from models import Citation

    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    arrests = scoped_query(Arrest).filter_by(civilian_id=civilian_id).all()
    citations = scoped_query(Citation).filter_by(civilian_id=civilian_id).all()
    warrants = scoped_query(Warrant).filter_by(civilian_id=civilian_id).all()

    return {
        'civilian_id': civilian_id,
        'name': civilian.full_name,
        'total_arrests': len(arrests),
        'total_citations': len(citations),
        'active_warrants': len([w for w in warrants if w.warrant_status == 'Active']),
        'arrests': [a.arrest_id for a in arrests],
        'citations': [c.citation_id for c in citations],
        'warrants': [w.warrant_id for w in warrants],
        'criminal_background': civilian.criminal_background,
    }


def link_bolo_to_dispatch_call(bolo_id, call_id):
    """Link a BOLO to a dispatch call."""
    from models import DispatchCall

    bolo = scoped_query(Bolo).filter_by(bolo_id=bolo_id).first()
    call = DispatchCall.query.filter_by(call_id=call_id).first()

    if not bolo or not call:
        return None

    call.description = f"{call.description}\n[BOLO ALERT: {bolo.suspect_name}]"
    call.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return call
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to link BOLO to call: {e}')
        raise


def update_dmv_status_from_citation(civilian_id, citation_count):
    """Update DMV status based on citation count."""
    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    if citation_count >= 5:
        civilian.driver_license_status = 'Suspended'
    elif citation_count >= 3:
        civilian.driver_license_status = 'Restricted'

    civilian.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return civilian
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to update DMV status: {e}')
        raise


def create_family_relationship(civilian_id1, civilian_id2, relationship):
    """Create family relationship between civilians."""
    return create_known_associate(civilian_id1, civilian_id2, f"Family: {relationship}")


def create_employment_relationship(civilian_id, business_id):
    """Link civilian to business as employee."""
    from models import Business

    business = Business.query.filter_by(business_id=business_id).first()
    if not business:
        return None

    if business.employees:
        business.employees += 1
    else:
        business.employees = 1

    business.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return create_known_associate(civilian_id, business_id, f"Employed at {business.business_name}")
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create employment: {e}')
        raise
