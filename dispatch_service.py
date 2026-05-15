import secrets
import logging
from datetime import datetime
from database import db
from community_service import get_current_community_id, scoped_query
from models import DispatchCall, OfficerSession

logger = logging.getLogger(__name__)

DISPATCH_CODES = {
    '10-1': 'Unable to copy',
    '10-2': 'Signal good',
    '10-3': 'Stop transmitting',
    '10-4': 'Acknowledged',
    '10-5': 'Relay',
    '10-6': 'Busy',
    '10-7': 'Out of service',
    '10-8': 'In service',
    '10-9': 'Repeat',
    '10-10': 'Fight in progress',
    '10-11': 'Dog case',
    '10-12': 'Standby',
    '10-13': 'Weather/Road report',
    '10-14': 'Prowler report',
    '10-15': 'Civil unrest',
    '10-16': 'Domestic disturbance',
    '10-17': 'Gang activity',
    '10-18': 'Shooting',
    '10-19': 'Return to station',
    '10-20': 'Location',
    '10-21': 'Call by phone',
    '10-22': 'Disregard',
    '10-23': 'Arrived at scene',
    '10-24': 'Assignment completed',
    '10-25': 'Report in person',
    '10-26': 'Detaining subject',
    '10-27': 'Driver\'s license check',
    '10-28': 'Vehicle registration check',
    '10-29': 'Check for wanted',
    '10-30': 'Illegal use of radio',
    '10-31': 'Crime in progress',
    '10-32': 'Man with gun',
    '10-33': 'Emergency traffic',
    '10-34': 'Riot',
    '10-35': 'Major crime alert',
    '10-36': 'Correct time',
    '10-37': 'Investigate suspicious vehicle',
    '10-38': 'Stopping suspicious vehicle',
    '10-39': 'Urgent - use light and siren',
    '10-40': 'Silent run - no light or siren',
    '10-41': 'Beginning tour of duty',
    '10-42': 'Ending tour of duty',
    '10-43': 'Information',
    '10-44': 'Permission to leave',
    '10-45': 'Animal carcass',
    '10-46': 'Assist motorist',
    '10-47': 'Emergency room call',
    '10-48': 'Traffic control',
    '10-49': 'Traffic light out',
    '10-50': 'Accident',
    '10-51': 'Wrecker needed',
    '10-52': 'Ambulance needed',
    '10-53': 'Road blocked',
    '10-54': 'Livestock on roadway',
    '10-55': 'Intoxicated driver',
    '10-56': 'Intoxicated pedestrian',
    '10-57': 'Hit and run',
    '10-58': 'Direct traffic',
    '10-59': 'Escort',
    '10-60': 'Squad in vicinity',
    '10-61': 'Personnel in area',
    '10-62': 'Reply to all units',
    '10-63': 'Prepare to copy',
    '10-64': 'Found property',
    '10-65': 'Stolen property',
    '10-66': 'Suspicious person',
    '10-67': 'Person calling for help',
    '10-68': 'Dispatch info',
    '10-69': 'Message received',
    '10-70': 'Fire alarm',
    '10-71': 'Structure fire',
    '10-72': 'Brush fire',
    '10-73': 'Smoke report',
    '10-74': 'Explosion',
    '10-75': 'Report available',
    '10-76': 'En route',
    '10-77': 'ETA',
    '10-78': 'Need assistance',
    '10-79': 'Notify coroner',
    '10-80': 'Chase in progress',
    '10-81': 'Breathalyzer',
    '10-82': 'Reserve lodging',
    '10-83': 'Work school crossing',
    '10-84': 'If meeting, advise',
    '10-85': 'Delayed report',
    '10-86': 'Officer/Operator on duty',
    '10-87': 'Pick up/Distribute checks',
    '10-88': 'Advise present location',
    '10-89': 'Bomb threat',
    '10-90': 'Bank alarm',
    '10-91': 'Pick pocket',
    '10-92': 'Improperly parked vehicle',
    '10-93': 'Blockade',
    '10-94': 'Drag racing',
    '10-95': 'Prisoner in custody',
    '10-96': 'Mental subject',
    '10-97': 'Check signal/Welfare check',
    '10-98': 'Prison/Jail break',
    '10-99': 'Officer needs help/Officer down',
}

CALL_TYPES = [
    'Accident', 'Assault', 'Burglary', 'Disturbance', 'Drug Activity',
    'Fraud', 'Gang Activity', 'Homicide', 'Robbery', 'Shooting',
    'Stolen Vehicle', 'Suspicious Activity', 'Traffic Stop', 'Welfare Check',
    'Domestic Violence', 'Trespassing', 'Vandalism', 'Weapons Violation',
    'DUI', 'Hit and Run', 'Noise Complaint', 'Parking Violation'
]

OFFICER_STATUSES = {
    '10-8': 'Available',
    '10-7': 'Out of Service',
    '10-6': 'Busy',
    'En Route': 'En Route',
    'On Scene': 'On Scene',
    '10-15': 'Transport',
    'In Pursuit': 'In Pursuit',
    'Down': 'Officer Down',
}


def create_dispatch_call(caller_name, location, call_type, description, priority='Medium'):
    """Create a new dispatch call."""
    call_id = f"CALL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    call = DispatchCall(
        community_id=get_current_community_id(),
        call_id=call_id,
        caller_name=caller_name,
        location=location,
        description=description,
        call_type=call_type,
        priority=priority,
        status='New',
    )

    try:
        db.session.add(call)
        db.session.commit()
        return call
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create dispatch call: {e}')
        raise


def get_active_calls():
    """Get all active dispatch calls."""
    calls = scoped_query(DispatchCall).filter(
        DispatchCall.status.in_(['New', 'Assigned', 'En Route', 'On Scene'])
    ).order_by(DispatchCall.created_at.desc()).all()

    return [{
        'call_id': c.call_id,
        'caller_name': c.caller_name,
        'location': c.location,
        'description': c.description,
        'call_type': c.call_type,
        'priority': c.priority,
        'status': c.status,
        'assigned_units': c.assigned_unit.split(',') if c.assigned_unit else [],
        'created_at': c.created_at.isoformat() if c.created_at else None,
        'updated_at': c.updated_at.isoformat() if c.updated_at else None,
    } for c in calls]


def get_officer_status(callsign):
    """Get officer status."""
    officer_session = scoped_query(OfficerSession).filter_by(callsign=callsign).first()
    if not officer_session:
        return None

    return {
        'callsign': officer_session.callsign,
        'officer_name': officer_session.officer_name,
        'department': officer_session.department,
        'status': officer_session.status,
        'logged_in_at': officer_session.logged_in_at.isoformat() if officer_session.logged_in_at else None,
    }


def update_officer_status(callsign, new_status):
    """Update officer status."""
    officer_session = scoped_query(OfficerSession).filter_by(callsign=callsign).first()
    if not officer_session:
        return None

    officer_session.status = new_status
    officer_session.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return officer_session
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to update officer status: {e}')
        raise


def assign_units_to_call(call_id, units):
    """Assign units to a dispatch call."""
    call = scoped_query(DispatchCall).filter_by(call_id=call_id).first()
    if not call:
        return None

    call.assigned_unit = ','.join(units) if isinstance(units, list) else units
    call.status = 'Assigned'
    call.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return call
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to assign units: {e}')
        raise


def close_dispatch_call(call_id, resolution):
    """Close a dispatch call."""
    call = scoped_query(DispatchCall).filter_by(call_id=call_id).first()
    if not call:
        return None

    call.status = 'Closed'
    call.description = f"{call.description}\n\nResolution: {resolution}"
    call.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return call
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to close dispatch call: {e}')
        raise
