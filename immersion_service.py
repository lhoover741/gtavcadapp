import secrets
import logging
from datetime import datetime, timedelta
from database import db
from community_service import scoped_query
from models import Civilian, Vehicle, Warrant, Bolo, DispatchCall, AuditLog

logger = logging.getLogger(__name__)

ALERT_TYPES = {
    'warrant_hit': {
        'icon': '⚠️',
        'color': 'red',
        'priority': 'Critical',
    },
    'stolen_vehicle': {
        'icon': '🚗',
        'color': 'orange',
        'priority': 'High',
    },
    'bolo_match': {
        'icon': '🔍',
        'color': 'yellow',
        'priority': 'High',
    },
    'safety_warning': {
        'icon': '⚡',
        'color': 'red',
        'priority': 'Critical',
    },
    'dispatch_call': {
        'icon': '📞',
        'color': 'blue',
        'priority': 'Medium',
    },
    'officer_down': {
        'icon': '🚨',
        'color': 'red',
        'priority': 'Critical',
    },
}


def generate_officer_safety_assessment(civilian_id):
    """Build an officer safety assessment from existing civilian model fields."""
    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    risk_score = 0
    warnings = []

    # Risk level scoring
    risk_map = {'Low': 1, 'Medium': 3, 'High': 6, 'Critical': 9}
    risk_score += risk_map.get(civilian.risk_level or 'Low', 1)

    # Active warrants
    warrants = scoped_query(Warrant).filter_by(
        civilian_id=civilian_id, warrant_status='Active'
    ).all()
    if warrants:
        risk_score += len(warrants) * 2
        warnings.append(f'{len(warrants)} active warrant(s) on file')

    # Gang affiliation
    if civilian.gang_affiliation and civilian.gang_affiliation not in ('None', 'None reported', ''):
        risk_score += 2
        warnings.append(f'Known gang affiliation: {civilian.gang_affiliation}')

    # Weapon access
    if civilian.weapon_access and civilian.weapon_access not in ('None', ''):
        risk_score += 2
        warnings.append(f'Weapon access: {civilian.weapon_access}')

    # Violence history
    if civilian.violence_history and civilian.violence_history not in ('None', ''):
        risk_score += 3
        warnings.append(f'Violence history: {civilian.violence_history}')

    # Parole / probation
    if civilian.parole_status and civilian.parole_status not in ('None', ''):
        risk_score += 1
        warnings.append(f'Parole status: {civilian.parole_status}')
    if civilian.probation_status and civilian.probation_status not in ('None', ''):
        risk_score += 1
        warnings.append(f'Probation status: {civilian.probation_status}')

    # Officer safety notes from profile
    if civilian.officer_safety_notes:
        warnings.append(civilian.officer_safety_notes[:120])

    # Determine risk level label
    if risk_score >= 10:
        risk_level = 'CRITICAL'
        recommendation = 'Request backup before approach. Treat as armed and dangerous.'
    elif risk_score >= 7:
        risk_level = 'HIGH'
        recommendation = 'Approach with caution. Request backup if available.'
    elif risk_score >= 4:
        risk_level = 'MEDIUM'
        recommendation = 'Standard precautions. Stay alert.'
    else:
        risk_level = 'LOW'
        recommendation = 'No immediate safety concerns identified.'

    return {
        'civilian_id': civilian_id,
        'name': civilian.full_name or f'{civilian.first_name or ""} {civilian.last_name or ""}'.strip(),
        'risk_score': risk_score,
        'risk_level': risk_level,
        'warnings': warnings,
        'recommendation': recommendation,
    }


def create_mdt_alert(alert_type, officer_id, title, message, data=None):
    """Create an MDT alert for an officer."""
    alert_id = f"ALERT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    alert_config = ALERT_TYPES.get(alert_type, ALERT_TYPES['dispatch_call'])

    alert = {
        'alert_id': alert_id,
        'type': alert_type,
        'officer_id': officer_id,
        'title': title,
        'message': message,
        'icon': alert_config['icon'],
        'color': alert_config['color'],
        'priority': alert_config['priority'],
        'timestamp': datetime.utcnow().isoformat(),
        'data': data or {},
        'read': False,
    }

    return alert


def generate_warrant_hit_alert(plate):
    """Generate alert when vehicle owner has active warrants."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle or not vehicle.owner_civilian_id:
        return None

    warrants = scoped_query(Warrant).filter_by(
        civilian_id=vehicle.owner_civilian_id,
        warrant_status='Active'
    ).all()

    if not warrants:
        return None

    civilian = scoped_query(Civilian).filter_by(civilian_id=vehicle.owner_civilian_id).first()

    return create_mdt_alert(
        'warrant_hit',
        'dispatch',
        f'⚠️ WARRANT HIT: {civilian.full_name if civilian else "Unknown"}',
        f'Vehicle {plate} owner has {len(warrants)} active warrant(s)',
        {
            'plate': plate,
            'civilian_id': vehicle.owner_civilian_id,
            'civilian_name': civilian.full_name if civilian else 'Unknown',
            'warrant_count': len(warrants),
            'warrants': [w.warrant_id for w in warrants],
        }
    )


def generate_stolen_vehicle_alert(plate):
    """Generate alert when stolen vehicle is spotted."""
    vehicle = scoped_query(Vehicle).filter_by(plate=plate).first()
    if not vehicle or not vehicle.stolen_flag:
        return None

    return create_mdt_alert(
        'stolen_vehicle',
        'dispatch',
        f'🚗 STOLEN VEHICLE ALERT: {vehicle.make} {vehicle.model}',
        f'Stolen vehicle spotted: {plate} - {vehicle.color} {vehicle.make} {vehicle.model}',
        {
            'plate': plate,
            'vehicle_id': vehicle.vehicle_id,
            'make': vehicle.make,
            'model': vehicle.model,
            'color': vehicle.color,
            'vin': vehicle.vin,
        }
    )


def generate_bolo_match_alert(civilian_id):
    """Generate alert when BOLO suspect is identified."""
    civilian = scoped_query(Civilian).filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    name = civilian.full_name or f'{civilian.first_name or ""} {civilian.last_name or ""}'.strip()

    bolos = Bolo.query.filter(
        Bolo.suspect_name.ilike(f'%{name}%'),
        Bolo.status == 'Active'
    ).all()

    if not bolos:
        return None

    return create_mdt_alert(
        'bolo_match',
        'dispatch',
        f'🔍 BOLO MATCH: {name}',
        f'Suspect matching BOLO identified: {name} at {civilian.address}',
        {
            'civilian_id': civilian_id,
            'civilian_name': name,
            'address': civilian.address,
            'bolo_count': len(bolos),
            'bolos': [b.bolo_id for b in bolos],
        }
    )


def generate_safety_warning_alert(civilian_id, officer_id):
    """Generate officer safety warning."""
    assessment = generate_officer_safety_assessment(civilian_id)
    if not assessment or assessment['risk_score'] < 5:
        return None

    return create_mdt_alert(
        'safety_warning',
        officer_id,
        f'⚡ OFFICER SAFETY WARNING: {assessment["name"]}',
        f'Risk Level: {assessment["risk_level"]} - {", ".join(assessment["warnings"][:2])}',
        {
            'civilian_id': civilian_id,
            'civilian_name': assessment['name'],
            'risk_score': assessment['risk_score'],
            'risk_level': assessment['risk_level'],
            'warnings': assessment['warnings'],
            'recommendation': assessment['recommendation'],
        }
    )


def generate_dispatch_audio_log(call_id):
    """Generate realistic dispatch audio log entry."""
    call = DispatchCall.query.filter_by(call_id=call_id).first()
    if not call:
        return None

    log_id = f"AUDIO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    # Simulate dispatch radio traffic
    radio_traffic = [
        f"Dispatch: All units, we have a {call.priority} priority call",
        f"Location: {call.location}",
        f"Description: {(call.description or '')[:100]}",
        f"Units responding, please advise ETA",
    ]

    log = {
        'log_id': log_id,
        'call_id': call_id,
        'timestamp': call.created_at.isoformat() if call.created_at else datetime.utcnow().isoformat(),
        'priority': call.priority,
        'location': call.location,
        'radio_traffic': radio_traffic,
        'duration_seconds': len(radio_traffic) * 5,  # Estimate 5 seconds per transmission
    }

    return log


def get_active_alerts(officer_id=None, limit=20):
    """Get active alerts for officer or all alerts."""
    alerts = []

    # Get all active warrants and generate alerts
    warrants = scoped_query(Warrant).filter_by(warrant_status='Active').limit(5).all()
    for warrant in warrants:
        alert = create_mdt_alert(
            'warrant_hit',
            officer_id or 'dispatch',
            f'⚠️ ACTIVE WARRANT: {warrant.warrant_name}',
            f'Charges: {warrant.warrant_charges}',
            {
                'warrant_id': warrant.warrant_id,
                'charges': warrant.warrant_charges,
            }
        )
        alerts.append(alert)

    # Get all active BOLOs and generate alerts
    bolos = scoped_query(Bolo).filter_by(status='Active').limit(5).all()
    for bolo in bolos:
        alert = create_mdt_alert(
            'bolo_match',
            officer_id or 'dispatch',
            f'🔍 ACTIVE BOLO: {bolo.suspect_name}',
            f'Threat Level: {bolo.threat_level}',
            {
                'bolo_id': bolo.bolo_id,
                'suspect': bolo.suspect_name,
                'threat_level': bolo.threat_level,
            }
        )
        alerts.append(alert)

    # Get active dispatch calls
    calls = DispatchCall.query.filter(
        DispatchCall.status.in_(['New', 'Assigned', 'En Route'])
    ).limit(5).all()

    for call in calls:
        alert = create_mdt_alert(
            'dispatch_call',
            officer_id or 'dispatch',
            f'📞 DISPATCH CALL: {call.location}',
            f'Priority: {call.priority} - {(call.description or "")[:80]}',
            {
                'call_id': call.call_id,
                'location': call.location,
                'priority': call.priority,
            }
        )
        alerts.append(alert)

    # Sort by priority then timestamp (Critical first, then most recent)
    priority_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
    alerts.sort(key=lambda x: (priority_order.get(x['priority'], 4), x['timestamp']), reverse=False)
    alerts.sort(key=lambda x: priority_order.get(x['priority'], 4))

    return alerts[:limit]


def get_dispatch_audio_logs(limit=20):
    """Get recent dispatch audio logs."""
    calls = DispatchCall.query.order_by(DispatchCall.created_at.desc()).limit(limit).all()

    logs = []
    for call in calls:
        log = generate_dispatch_audio_log(call.call_id)
        if log:
            logs.append(log)

    return logs


def create_realistic_timestamp(offset_minutes=0):
    """Create realistic timestamp with optional offset."""
    return (datetime.utcnow() + timedelta(minutes=offset_minutes)).isoformat()


def get_incident_timeline(call_id):
    """Get timeline of incident from dispatch to resolution."""
    call = DispatchCall.query.filter_by(call_id=call_id).first()
    if not call:
        return None

    base_time = call.created_at or datetime.utcnow()
    timeline = []

    # Call received
    timeline.append({
        'time': base_time.isoformat(),
        'event': 'Call Received',
        'description': f'Dispatch received call from {call.caller_name}',
        'status': 'completed',
    })

    # Units assigned
    if call.assigned_unit:
        timeline.append({
            'time': (base_time + timedelta(minutes=1)).isoformat(),
            'event': 'Units Assigned',
            'description': f'Units assigned: {call.assigned_unit}',
            'status': 'completed',
        })

    # En route
    if call.status in ['En Route', 'On Scene', 'Closed']:
        timeline.append({
            'time': (base_time + timedelta(minutes=3)).isoformat(),
            'event': 'En Route',
            'description': 'Units responding to scene',
            'status': 'completed',
        })

    # On scene
    if call.status in ['On Scene', 'Closed']:
        timeline.append({
            'time': (base_time + timedelta(minutes=5)).isoformat(),
            'event': 'On Scene',
            'description': 'Units arrived at location',
            'status': 'completed',
        })

    # Closed / pending
    if call.status == 'Closed':
        timeline.append({
            'time': (base_time + timedelta(minutes=15)).isoformat(),
            'event': 'Incident Closed',
            'description': 'Incident resolved',
            'status': 'completed',
        })
    else:
        timeline.append({
            'time': (base_time + timedelta(minutes=15)).isoformat(),
            'event': 'Pending Resolution',
            'description': 'Awaiting incident resolution',
            'status': 'pending',
        })

    return timeline
