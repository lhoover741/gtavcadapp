import json
import random
import logging
from datetime import datetime, timedelta
from database import db
from models import Civilian

logger = logging.getLogger(__name__)

ADDICTIONS = {
    'None': {'severity': 'None', 'behaviors': []},
    'Alcohol': {
        'severity': ['Mild', 'Moderate', 'Severe'],
        'behaviors': ['Frequent bars', 'DUI history', 'Erratic behavior', 'Slurred speech'],
    },
    'Drugs': {
        'severity': ['Mild', 'Moderate', 'Severe'],
        'behaviors': ['Track marks', 'Paranoid', 'Aggressive', 'Theft history'],
    },
    'Both': {
        'severity': ['Moderate', 'Severe'],
        'behaviors': ['Unpredictable', 'Dangerous', 'Criminal history', 'Violent'],
    },
}

MEDICAL_CONDITIONS = [
    'Diabetes', 'Hypertension', 'Asthma', 'PTSD', 'Depression', 'Anxiety',
    'Bipolar Disorder', 'Schizophrenia', 'Heart Disease', 'Epilepsy',
    'Chronic Pain', 'Sleep Apnea', 'COPD', 'Arthritis', 'None',
]

MEDICATIONS = [
    'Metformin', 'Lisinopril', 'Albuterol', 'Sertraline', 'Alprazolam',
    'Methadone', 'Suboxone', 'Lithium', 'Risperidone', 'Morphine',
    'Ibuprofen', 'Aspirin', 'Omeprazole', 'Atorvastatin', 'None',
]

ALLERGIES = [
    'Penicillin', 'Sulfa', 'Latex', 'Peanuts', 'Shellfish', 'Dairy',
    'Gluten', 'Eggs', 'Tree Nuts', 'Soy', 'None',
]

DRIVING_VIOLATIONS = [
    'Speeding', 'Reckless Driving', 'DUI', 'Hit and Run', 'Expired License',
    'No Insurance', 'Suspended License', 'Failure to Yield', 'Running Red Light',
    'Improper Lane Change', 'Texting While Driving', 'Expired Registration',
]

EMPLOYMENT_HISTORY = [
    {'title': 'Construction Worker', 'duration': '2-5 years'},
    {'title': 'Mechanic', 'duration': '1-3 years'},
    {'title': 'Retail Clerk', 'duration': '6 months - 2 years'},
    {'title': 'Fast Food Worker', 'duration': '3 months - 1 year'},
    {'title': 'Security Guard', 'duration': '1-4 years'},
    {'title': 'Bartender', 'duration': '1-3 years'},
    {'title': 'Taxi Driver', 'duration': '2-5 years'},
    {'title': 'Delivery Driver', 'duration': '1-2 years'},
    {'title': 'Warehouse Worker', 'duration': '1-3 years'},
    {'title': 'Janitor', 'duration': '6 months - 2 years'},
]


def generate_employment_history(count=3):
    """Generate realistic employment history."""
    history = []
    current_year = datetime.now().year

    for _ in range(count):
        job = random.choice(EMPLOYMENT_HISTORY)
        years_ago = random.randint(1, 15)

        history.append({
            'title': job['title'],
            'duration': job['duration'],
            'year': current_year - years_ago,
            'reason_left': random.choice(['Laid off', 'Quit', 'Fired', 'Relocated', 'Better opportunity']),
        })

    return history


def generate_driving_history(violation_count=0):
    """Generate driving history with violations."""
    history = []

    for _ in range(violation_count):
        violation = random.choice(DRIVING_VIOLATIONS)
        years_ago = random.randint(1, 10)

        history.append({
            'violation': violation,
            'year': datetime.now().year - years_ago,
            'fine': random.randint(100, 1000),
            'points': random.randint(1, 6),
        })

    return history


def generate_addiction_profile():
    """Generate addiction profile."""
    addiction_type = random.choice(list(ADDICTIONS.keys()))
    profile = ADDICTIONS[addiction_type]

    if addiction_type == 'None':
        return {
            'type': 'None',
            'severity': 'None',
            'behaviors': [],
            'treatment_status': 'N/A',
        }

    severity = random.choice(profile['severity'])
    behaviors = random.sample(profile['behaviors'], k=random.randint(1, len(profile['behaviors'])))

    return {
        'type': addiction_type,
        'severity': severity,
        'behaviors': behaviors,
        'treatment_status': random.choice(['Untreated', 'In Treatment', 'In Recovery', 'Relapsed']),
    }


def generate_medical_profile():
    """Generate medical profile."""
    conditions = []
    medications = []
    allergies = []

    # 40% chance of medical condition
    if random.random() < 0.4:
        conditions = random.sample(
            [c for c in MEDICAL_CONDITIONS if c != 'None'],
            k=random.randint(1, 2),
        )

    # 30% chance of medications
    if random.random() < 0.3:
        medications = random.sample(
            [m for m in MEDICATIONS if m != 'None'],
            k=random.randint(1, 3),
        )

    # 20% chance of allergies
    if random.random() < 0.2:
        allergies = random.sample(
            [a for a in ALLERGIES if a != 'None'],
            k=random.randint(1, 2),
        )

    return {
        'conditions': conditions or ['None'],
        'medications': medications or ['None'],
        'allergies': allergies or ['None'],
    }


def generate_weapon_permit():
    """Generate weapon permit status."""
    if random.random() < 0.15:  # 15% have permits
        return {
            'has_permit': True,
            'type': random.choice(['Handgun', 'Rifle', 'Shotgun', 'Multiple']),
            'issued_date': (datetime.now() - timedelta(days=random.randint(365, 3650))).isoformat(),
            'expires_date': (datetime.now() + timedelta(days=random.randint(365, 3650))).isoformat(),
        }

    return {
        'has_permit': False,
        'type': None,
        'issued_date': None,
        'expires_date': None,
    }


def generate_emergency_contact():
    """Generate emergency contact."""
    from world_realism_service import generate_name

    contact_name = generate_name()['full_name']
    relationships = ['Spouse', 'Parent', 'Sibling', 'Child', 'Friend', 'Relative']

    return {
        'name': contact_name,
        'phone': f"555-{random.randint(1000, 9999)}",
        'relationship': random.choice(relationships),
    }


def enhance_civilian_with_advanced_data(civilian_id):
    """Add advanced data to existing civilian."""
    civilian = Civilian.query.filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    # Generate advanced data
    employment = generate_employment_history(random.randint(2, 4))
    addiction = generate_addiction_profile()
    medical = generate_medical_profile()
    weapon = generate_weapon_permit()
    emergency = generate_emergency_contact()

    # Determine driving violations based on criminal history
    violation_count = 0
    if civilian.criminal_background:
        violation_count = min(len(civilian.criminal_background.split('\n')), 5)

    driving = generate_driving_history(violation_count)

    # Update civilian — employment_history already exists on the model
    civilian.employment_history = json.dumps(employment)
    civilian.addiction_status = addiction['type']
    civilian.addiction_severity = addiction['severity']
    civilian.weapon_permit = weapon['has_permit']
    civilian.weapon_permit_type = weapon['type']
    civilian.driving_history = json.dumps(driving)
    civilian.insurance_status = 'Valid' if random.random() < 0.8 else random.choice(['Lapsed', 'None'])
    civilian.emergency_contact_name = emergency['name']
    civilian.emergency_contact_phone = emergency['phone']
    civilian.emergency_contact_relationship = emergency['relationship']
    civilian.medical_conditions = json.dumps(medical['conditions'])
    civilian.medications = json.dumps(medical['medications'])
    civilian.allergies = json.dumps(medical['allergies'])
    civilian.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        return civilian
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to enhance civilian: {e}')
        raise


def get_civilian_full_profile(civilian_id):
    """Get complete civilian profile with all advanced data."""
    civilian = Civilian.query.filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    return {
        'civilian_id': civilian.civilian_id,
        'name': civilian.full_name,
        'age': civilian.age,
        'gender': civilian.gender,
        'race': civilian.race,
        'address': civilian.address,
        'phone': civilian.phone_number,
        'occupation': civilian.occupation,
        'gang_affiliation': civilian.gang_affiliation,
        'risk_level': civilian.risk_level,
        'employment_history': json.loads(civilian.employment_history) if civilian.employment_history else [],
        'addiction': {
            'type': civilian.addiction_status,
            'severity': civilian.addiction_severity,
        },
        'medical': {
            'conditions': json.loads(civilian.medical_conditions) if civilian.medical_conditions else [],
            'medications': json.loads(civilian.medications) if civilian.medications else [],
            'allergies': json.loads(civilian.allergies) if civilian.allergies else [],
        },
        'weapon_permit': {
            'has_permit': civilian.weapon_permit,
            'type': civilian.weapon_permit_type,
        },
        'driving_history': json.loads(civilian.driving_history) if civilian.driving_history else [],
        'insurance_status': civilian.insurance_status,
        'emergency_contact': {
            'name': civilian.emergency_contact_name,
            'phone': civilian.emergency_contact_phone,
            'relationship': civilian.emergency_contact_relationship,
        },
        'criminal_background': civilian.criminal_background,
        'parole_status': civilian.parole_status,
        'probation_status': civilian.probation_status,
        'warrant_risk': civilian.warrant_risk,
        'officer_safety_notes': civilian.officer_safety_notes,
        'driver_license_status': civilian.driver_license_status,
    }


def generate_officer_safety_assessment(civilian_id):
    """Generate officer safety assessment based on profile."""
    civilian = Civilian.query.filter_by(civilian_id=civilian_id).first()
    if not civilian:
        return None

    warnings = []
    risk_score = 0

    # Check addiction
    if civilian.addiction_status and civilian.addiction_status != 'None':
        warnings.append(f"Substance abuse: {civilian.addiction_status} ({civilian.addiction_severity})")
        risk_score += 2 if civilian.addiction_severity == 'Severe' else 1

    # Check weapon permit
    if civilian.weapon_permit:
        warnings.append(f"Armed: {civilian.weapon_permit_type} permit holder")
        risk_score += 2

    # Check criminal history
    if civilian.criminal_background and len(civilian.criminal_background) > 100:
        warnings.append("Extensive criminal history")
        risk_score += 2

    # Check violence history
    if civilian.violence_history and civilian.violence_history != 'None':
        warnings.append(f"Violence history: {civilian.violence_history}")
        risk_score += 3

    # Check gang affiliation
    if civilian.gang_affiliation and civilian.gang_affiliation != 'None':
        warnings.append(f"Gang affiliated: {civilian.gang_affiliation}")
        risk_score += 2

    # Check active warrants
    from models import Warrant
    active_warrants = Warrant.query.filter(
        Warrant.warrant_name.ilike(f'%{civilian.full_name}%'),
        Warrant.warrant_status == 'Active',
    ).count()

    if active_warrants > 0:
        warnings.append(f"Active warrants: {active_warrants}")
        risk_score += 3

    return {
        'civilian_id': civilian_id,
        'name': civilian.full_name,
        'risk_score': min(risk_score, 10),  # Cap at 10
        'risk_level': (
            'Critical' if risk_score >= 8
            else 'High' if risk_score >= 5
            else 'Medium' if risk_score >= 2
            else 'Low'
        ),
        'warnings': warnings,
        'recommendation': (
            'Approach with extreme caution' if risk_score >= 8
            else 'Use caution' if risk_score >= 5
            else 'Standard approach'
        ),
    }
