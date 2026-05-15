import secrets
import logging
from datetime import datetime, timedelta
from database import db
from community_service import get_current_community_id, scoped_query
from models import License, Vehicle, Civilian

logger = logging.getLogger(__name__)

LICENSE_TYPES = [
    'Class A - Motorcycle',
    'Class B - Passenger Vehicle',
    'Class C - Commercial',
    'Class D - Heavy Truck',
    'Chauffeur License',
    'Taxi License',
    'Commercial Driver License',
]

LICENSE_STATUSES = {
    'Valid': 'Active and valid',
    'Suspended': 'License suspended',
    'Revoked': 'License revoked',
    'Expired': 'License expired',
    'Pending': 'Pending approval',
}

REGISTRATION_STATUSES = {
    'Valid': 'Current registration',
    'Expired': 'Registration expired',
    'Suspended': 'Registration suspended',
    'Revoked': 'Registration revoked',
}

INSURANCE_STATUSES = {
    'Valid': 'Current insurance',
    'Expired': 'Insurance expired',
    'Lapsed': 'Insurance lapsed',
    'None': 'No insurance',
}

def create_license(civilian_id, license_type, issue_date=None, expiry_date=None):
    """Create a driver's license."""
    license_id = f"LIC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    if not issue_date:
        issue_date = datetime.now().date()
    if not expiry_date:
        expiry_date = (datetime.now() + timedelta(days=365*5)).date()

    license = License(
        community_id=get_current_community_id(),
        license_id=license_id,
        owner_name=f"CIV-{civilian_id}",
        license_type=license_type,
        status='Valid',
        issued_date=issue_date.isoformat() if hasattr(issue_date, 'isoformat') else str(issue_date),
        expiry_date=expiry_date.isoformat() if hasattr(expiry_date, 'isoformat') else str(expiry_date),
    )

    try:
        db.session.add(license)
        db.session.commit()
        return license
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create license: {e}')
        raise

def get_license_by_id(license_id):
    """Get license by ID."""
    return License.query.filter_by(license_id=license_id).first()

def check_license_status(civilian_id):
    """Check license status for a civilian."""
    licenses = License.query.filter(License.owner_name.like(f'%{civilian_id}%')).all()

    if not licenses:
        return {'status': 'No License', 'licenses': []}

    result = []
    for lic in licenses:
        result.append({
            'license_id': lic.license_id,
            'type': lic.license_type,
            'status': lic.status,
            'issued_date': lic.issued_date,
            'expiry_date': lic.expiry_date,
        })

    return {'status': 'Found', 'licenses': result}

def suspend_license(license_id, reason):
    """Suspend a driver's license."""
    license = License.query.filter_by(license_id=license_id).first()
    if not license:
        return None

    license.status = 'Suspended'
    license.notes = f"Suspended: {reason}"
    license.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return license
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to suspend license: {e}')
        raise

def revoke_license(license_id, reason):
    """Revoke a driver's license."""
    license = License.query.filter_by(license_id=license_id).first()
    if not license:
        return None

    license.status = 'Revoked'
    license.notes = f"Revoked: {reason}"
    license.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return license
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to revoke license: {e}')
        raise

def register_vehicle(owner_civilian_id, plate, make, model, color, vin=None):
    """Register a vehicle."""
    vehicle_id = f"VEH-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    vehicle = Vehicle(
        community_id=get_current_community_id(),
        vehicle_id=vehicle_id,
        owner_civilian_id=owner_civilian_id,
        plate=plate,
        vin=vin or f"VIN{secrets.token_hex(8).upper()}",
        make=make,
        model=model,
        color=color,
        registration_status='Valid',
        insurance_status='Valid',
        stolen_flag=False,
        impound_status='None',
    )

    try:
        db.session.add(vehicle)
        db.session.commit()
        return vehicle
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to register vehicle: {e}')
        raise

def lookup_vehicle_by_plate(plate):
    """Look up vehicle by license plate."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle:
        return None

    return {
        'vehicle_id': vehicle.vehicle_id,
        'plate': vehicle.plate,
        'make': vehicle.make,
        'model': vehicle.model,
        'color': vehicle.color,
        'vin': vehicle.vin,
        'owner_civilian_id': vehicle.owner_civilian_id,
        'registration_status': vehicle.registration_status,
        'insurance_status': vehicle.insurance_status,
        'stolen_flag': vehicle.stolen_flag,
        'impound_status': vehicle.impound_status,
    }

def lookup_vehicles_by_owner(civilian_id):
    """Look up all vehicles owned by a civilian."""
    vehicles = scoped_query(Vehicle).filter_by(owner_civilian_id=civilian_id).all()

    result = []
    for v in vehicles:
        result.append({
            'vehicle_id': v.vehicle_id,
            'plate': v.plate,
            'make': v.make,
            'model': v.model,
            'color': v.color,
            'registration_status': v.registration_status,
            'insurance_status': v.insurance_status,
            'stolen_flag': v.stolen_flag,
        })

    return result

def flag_stolen_vehicle(plate, report_date=None):
    """Flag a vehicle as stolen."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle:
        return None

    vehicle.stolen_flag = True
    vehicle.notes = f"Stolen: {report_date or datetime.now().isoformat()}"
    vehicle.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return vehicle
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to flag stolen vehicle: {e}')
        raise

def recover_stolen_vehicle(plate):
    """Mark a stolen vehicle as recovered."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle:
        return None

    vehicle.stolen_flag = False
    vehicle.notes = f"Recovered: {datetime.now().isoformat()}"
    vehicle.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return vehicle
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to recover vehicle: {e}')
        raise

def impound_vehicle(plate, reason):
    """Impound a vehicle."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle:
        return None

    vehicle.impound_status = 'Impounded'
    vehicle.notes = f"Impounded: {reason}"
    vehicle.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return vehicle
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to impound vehicle: {e}')
        raise

def release_impounded_vehicle(plate):
    """Release an impounded vehicle."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle:
        return None

    vehicle.impound_status = 'None'
    vehicle.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return vehicle
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to release vehicle: {e}')
        raise
