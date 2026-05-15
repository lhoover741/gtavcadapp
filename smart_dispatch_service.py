import secrets
import random
import logging
from datetime import datetime, timedelta
from database import db
from community_service import get_current_community_id, scoped_query
from models import DispatchCall, Civilian, Vehicle, Bolo, Warrant

logger = logging.getLogger(__name__)

CALL_SCENARIOS = {
    'robbery': {
        'types': ['Armed Robbery', 'Robbery in Progress', 'Robbery - Convenience Store', 'Robbery - Bank'],
        'descriptions': [
            'Suspect armed with {weapon}, demanding cash',
            'Multiple suspects, weapons visible',
            'Robbery in progress, suspect fleeing scene',
            'Robbery reported, suspect description: {description}',
        ],
        'priority': 'Critical',
    },
    'traffic_stop': {
        'types': ['Traffic Stop', 'Suspicious Vehicle', 'Vehicle Check'],
        'descriptions': [
            'Vehicle matching BOLO description',
            'Erratic driving, possible DUI',
            'Vehicle with expired registration',
            'Suspicious vehicle in area',
        ],
        'priority': 'Medium',
    },
    'shots_fired': {
        'types': ['Shots Fired', 'Drive-by Shooting', 'Active Shooter'],
        'descriptions': [
            'Multiple shots fired in area',
            'Drive-by shooting reported',
            'Gunshots heard, location: {location}',
            'Active shooter, civilians in danger',
        ],
        'priority': 'Critical',
    },
    'suspicious_person': {
        'types': ['Suspicious Person', 'Prowler', 'Loitering'],
        'descriptions': [
            'Suspicious person matching BOLO',
            'Person acting erratically',
            'Prowler reported in residential area',
            'Suspicious person with weapon',
        ],
        'priority': 'High',
    },
    'domestic': {
        'types': ['Domestic Disturbance', 'Domestic Violence', 'Family Dispute'],
        'descriptions': [
            'Domestic violence in progress',
            'Loud argument, possible weapons',
            'Domestic disturbance, caller in danger',
            'Family dispute, violence reported',
        ],
        'priority': 'High',
    },
    'pursuit': {
        'types': ['Vehicle Pursuit', 'Fleeing Police', 'High-Speed Chase'],
        'descriptions': [
            'Vehicle fleeing police, high speed',
            'Pursuit in progress, multiple units',
            'Suspect vehicle fleeing, dangerous driving',
            'Active pursuit, suspect armed',
        ],
        'priority': 'Critical',
    },
    'medical': {
        'types': ['Medical Emergency', 'Overdose', 'Injury Report'],
        'descriptions': [
            'Medical emergency, ambulance requested',
            'Possible overdose, unconscious subject',
            'Injury from assault, bleeding',
            'Medical emergency, caller distressed',
        ],
        'priority': 'High',
    },
}


def generate_smart_call(call_type=None):
    """Generate a realistic dispatch call with linked data."""
    if not call_type:
        call_type = random.choice(list(CALL_SCENARIOS.keys()))

    scenario = CALL_SCENARIOS.get(call_type, CALL_SCENARIOS['suspicious_person'])

    call_id = f"CALL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    # Get random location
    from world_realism_service import generate_address
    location = generate_address()

    # Get random suspect if available
    suspect_description = None
    linked_civilian = None
    linked_vehicle = None
    linked_bolo = None

    # 60% chance to link to existing civilian
    if random.random() < 0.6:
        civilians = Civilian.query.limit(100).all()
        if civilians:
            linked_civilian = random.choice(civilians)
            suspect_description = f"{linked_civilian.gender}, {linked_civilian.age} years old, {linked_civilian.race}"

            # Check if they have vehicles
            vehicles = scoped_query(Vehicle).filter_by(owner_civilian_id=linked_civilian.civilian_id).all()
            if vehicles:
                linked_vehicle = random.choice(vehicles)

            # Check if they have active BOLOs
            bolos = Bolo.query.filter(
                (Bolo.suspect_name.ilike(f'%{linked_civilian.full_name}%')) &
                (Bolo.status == 'Active')
            ).all()
            if bolos:
                linked_bolo = random.choice(bolos)

    # Generate description
    description_template = random.choice(scenario['descriptions'])
    description = description_template.format(
        weapon=random.choice(['handgun', 'rifle', 'knife', 'unknown weapon']),
        description=suspect_description or 'Unknown',
        location=location
    )

    call = DispatchCall(
        community_id=get_current_community_id(),
        call_id=call_id,
        caller_name=f"Caller {secrets.token_hex(2).upper()}",
        location=location,
        description=description,
        priority=scenario['priority'],
        status='New',
    )

    # Add metadata to notes
    call.notes = f"Type: {random.choice(scenario['types'])}"
    if linked_civilian:
        call.notes += f"\nSuspect: {linked_civilian.civilian_id}"
    if linked_vehicle:
        call.notes += f"\nVehicle: {linked_vehicle.plate}"
    if linked_bolo:
        call.notes += f"\nBOLO: {linked_bolo.bolo_id}"

    try:
        db.session.add(call)
        db.session.commit()
        return {
            'call_id': call.call_id,
            'location': call.location,
            'description': call.description,
            'priority': call.priority,
            'linked_civilian': linked_civilian.civilian_id if linked_civilian else None,
            'linked_vehicle': linked_vehicle.vehicle_id if linked_vehicle else None,
            'linked_bolo': linked_bolo.bolo_id if linked_bolo else None,
        }
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to generate call: {e}')
        raise


def get_active_calls_with_context():
    """Get active calls with full context."""
    calls = DispatchCall.query.filter(
        DispatchCall.status.in_(['New', 'Assigned', 'En Route', 'On Scene'])
    ).order_by(DispatchCall.created_at.desc()).all()

    result = []
    for call in calls:
        call_data = {
            'call_id': call.call_id,
            'location': call.location,
            'description': call.description,
            'priority': call.priority,
            'status': call.status,
            'created_at': call.created_at.isoformat() if call.created_at else None,
            'context': {
                'linked_civilian': None,
                'linked_vehicle': None,
                'linked_warrants': [],
                'linked_bolo': None,
            }
        }

        # Extract linked data from notes
        if call.notes:
            if 'Suspect:' in call.notes:
                civ_id = call.notes.split('Suspect: ')[1].split('\n')[0]
                civ = scoped_query(Civilian).filter_by(civilian_id=civ_id).first()
                if civ:
                    call_data['context']['linked_civilian'] = {
                        'civilian_id': civ.civilian_id,
                        'name': civ.full_name,
                        'risk_level': civ.risk_level,
                    }

                    # Get active warrants matching the civilian's name
                    warrants = Warrant.query.filter(
                        (Warrant.warrant_name.ilike(f'%{civ.full_name}%')) &
                        (Warrant.warrant_status == 'Active')
                    ).all()
                    call_data['context']['linked_warrants'] = [w.warrant_id for w in warrants]

            if 'Vehicle:' in call.notes:
                plate = call.notes.split('Vehicle: ')[1].split('\n')[0]
                veh = scoped_query(Vehicle).filter_by(plate=plate).first()
                if veh:
                    call_data['context']['linked_vehicle'] = {
                        'vehicle_id': veh.vehicle_id,
                        'plate': veh.plate,
                        'make': veh.make,
                        'color': veh.color,
                        'stolen': veh.stolen_flag,
                    }

            if 'BOLO:' in call.notes:
                bolo_id = call.notes.split('BOLO: ')[1].split('\n')[0]
                bolo = scoped_query(Bolo).filter_by(bolo_id=bolo_id).first()
                if bolo:
                    call_data['context']['linked_bolo'] = {
                        'bolo_id': bolo.bolo_id,
                        'suspect': bolo.suspect_name,
                        'threat_level': bolo.threat_level,
                    }

        result.append(call_data)

    return result


def auto_generate_calls(count=5):
    """Auto-generate multiple dispatch calls."""
    generated = []
    for _ in range(count):
        try:
            call = generate_smart_call()
            generated.append(call)
        except Exception as e:
            logger.error(f'Failed to generate call: {e}')

    return generated
