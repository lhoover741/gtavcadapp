import json
import random
import secrets
import logging
from datetime import datetime, timedelta
from database import db
from community_service import scoped_query
from models import Civilian
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def get_existing_civilian_names():
    """Get all existing civilian names from database."""
    civilians = scoped_query(Civilian).all()
    names = []
    for civ in civilians:
        if civ.full_name:
            names.append(civ.full_name.lower())
    return names


def check_name_similarity(new_name, existing_names, threshold=0.85):
    """Check if new name is too similar to existing names."""
    new_name_lower = new_name.lower()

    for existing_name in existing_names:
        similarity = SequenceMatcher(None, new_name_lower, existing_name).ratio()
        if similarity >= threshold:
            return True, existing_name

    return False, None


def generate_civilian_profile():
    """Generate a complete civilian profile with all advanced data."""
    from world_realism_service import generate_name, generate_address, generate_vehicle
    from advanced_civilian_service import (
        generate_employment_history,
        generate_addiction_profile,
        generate_medical_profile,
        generate_weapon_permit,
        generate_emergency_contact,
        generate_driving_history,
    )

    # Generate basic info
    name_data = generate_name()
    address = generate_address()
    vehicle = generate_vehicle()

    # Generate advanced data
    employment = generate_employment_history(random.randint(2, 4))
    addiction = generate_addiction_profile()
    medical = generate_medical_profile()
    weapon = generate_weapon_permit()
    emergency = generate_emergency_contact()
    driving = generate_driving_history(random.randint(0, 3))

    # Generate other attributes
    age = random.randint(18, 75)
    dob = (datetime.now() - timedelta(days=age * 365)).date()

    return {
        'first_name': name_data['first_name'],
        'last_name': name_data['last_name'],
        'full_name': name_data['full_name'],
        'gender': name_data['gender'],
        'age': age,
        'date_of_birth': dob.isoformat(),
        'race': random.choice(['African American', 'Hispanic/Latino', 'Caucasian', 'Asian', 'Middle Eastern', 'Mixed']),
        'address': address,
        'phone_number': f"555-{random.randint(1000, 9999)}",
        'occupation': random.choice([
            'Construction Worker', 'Mechanic', 'Taxi Driver', 'Security Guard', 'Bartender',
            'Waiter/Waitress', 'Retail Clerk', 'Delivery Driver', 'Bouncer', 'Stripper',
            'Drug Dealer', 'Hustler', 'Prostitute', 'Thief', 'Enforcer', 'Unemployed',
        ]),
        'biography': (
            f"Resident of {address.split(',')[-1].strip()}. "
            + random.choice([
                'Recently moved to the city.',
                'Long-time resident.',
                'Just trying to get by.',
                'Has connections in the area.',
            ])
        ),
        'employment_history': employment,
        'addiction_status': addiction['type'],
        'addiction_severity': addiction['severity'],
        'medical_conditions': medical['conditions'],
        'medications': medical['medications'],
        'allergies': medical['allergies'],
        'weapon_permit': weapon['has_permit'],
        'weapon_permit_type': weapon['type'],
        'driving_history': driving,
        'insurance_status': 'Valid' if random.random() < 0.8 else random.choice(['Lapsed', 'None']),
        'emergency_contact_name': emergency['name'],
        'emergency_contact_phone': emergency['phone'],
        'emergency_contact_relationship': emergency['relationship'],
        'gang_affiliation': random.choice(['None', 'Grove Street Families', 'Ballas', 'Vagos', 'Mafia']),
        'risk_level': random.choice(['Low', 'Medium', 'High', 'Critical']),
        'vehicle': vehicle,
    }


def save_civilian_to_database(profile_data):
    """Save generated civilian profile to database."""
    civilian_id = f"CIV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    # Parse ISO date string to a date object for the Date column
    raw_dob = profile_data.get('date_of_birth')
    dob = None
    if raw_dob:
        try:
            dob = datetime.strptime(raw_dob, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            dob = None

    civilian = Civilian(
        civilian_id=civilian_id,
        first_name=profile_data['first_name'],
        last_name=profile_data['last_name'],
        full_name=profile_data['full_name'],
        age=profile_data['age'],
        gender=profile_data['gender'],
        race=profile_data['race'],
        address=profile_data['address'],
        phone_number=profile_data['phone_number'],
        occupation=profile_data['occupation'],
        biography=profile_data['biography'],
        gang_affiliation=profile_data['gang_affiliation'],
        risk_level=profile_data['risk_level'],
        date_of_birth=dob,
        addiction_status=profile_data['addiction_status'],
        addiction_severity=profile_data['addiction_severity'],
        weapon_permit=profile_data['weapon_permit'],
        weapon_permit_type=profile_data['weapon_permit_type'],
        insurance_status=profile_data['insurance_status'],
        emergency_contact_name=profile_data['emergency_contact_name'],
        emergency_contact_phone=profile_data['emergency_contact_phone'],
        emergency_contact_relationship=profile_data['emergency_contact_relationship'],
        medical_conditions=json.dumps(profile_data['medical_conditions']),
        medications=json.dumps(profile_data['medications']),
        allergies=json.dumps(profile_data['allergies']),
        employment_history=json.dumps(profile_data['employment_history']),
        driving_history=json.dumps(profile_data['driving_history']),
        ai_generated=True,
    )

    try:
        db.session.add(civilian)
        db.session.commit()
        return civilian
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to save civilian: {e}')
        raise


def generate_and_save_civilian():
    """Generate a civilian and save to database with duplicate prevention."""
    # Get existing names
    existing_names = get_existing_civilian_names()

    # Generate profile
    profile = generate_civilian_profile()

    # Check for duplicates
    is_duplicate, similar_name = check_name_similarity(profile['full_name'], existing_names)

    if is_duplicate:
        logger.warning(
            f"Duplicate name detected: {profile['full_name']} similar to {similar_name}, regenerating..."
        )
        max_attempts = 5
        for attempt in range(max_attempts):
            profile = generate_civilian_profile()
            is_duplicate, similar_name = check_name_similarity(profile['full_name'], existing_names)
            if not is_duplicate:
                break

        if is_duplicate:
            raise ValueError(f"Could not generate unique name after {max_attempts} attempts")

    # Save to database
    civilian = save_civilian_to_database(profile)

    return {
        'civilian_id': civilian.civilian_id,
        'first_name': civilian.first_name,
        'last_name': civilian.last_name,
        'full_name': civilian.full_name,
        'age': civilian.age,
        'gender': civilian.gender,
        'race': civilian.race,
        'address': civilian.address,
        'phone_number': civilian.phone_number,
        'occupation': civilian.occupation,
        'biography': civilian.biography,
        'gang_affiliation': civilian.gang_affiliation,
        'risk_level': civilian.risk_level,
        'date_of_birth': civilian.date_of_birth.isoformat() if civilian.date_of_birth else None,
        'addiction_status': civilian.addiction_status,
        'addiction_severity': civilian.addiction_severity,
        'weapon_permit': civilian.weapon_permit,
        'weapon_permit_type': civilian.weapon_permit_type,
        'insurance_status': civilian.insurance_status,
        'emergency_contact_name': civilian.emergency_contact_name,
        'emergency_contact_phone': civilian.emergency_contact_phone,
        'emergency_contact_relationship': civilian.emergency_contact_relationship,
        'medical_conditions': json.loads(civilian.medical_conditions) if civilian.medical_conditions else [],
        'medications': json.loads(civilian.medications) if civilian.medications else [],
        'allergies': json.loads(civilian.allergies) if civilian.allergies else [],
        'driving_history': json.loads(civilian.driving_history) if civilian.driving_history else [],
    }
