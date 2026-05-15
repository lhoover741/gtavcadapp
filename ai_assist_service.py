import random
import logging
from datetime import datetime, timedelta
from ai_character_engine import generate_character
from world_realism_service import generate_name, generate_address

logger = logging.getLogger(__name__)


def check_name_exists(first_name, last_name):
    """Check if name already exists in database."""
    from database import db
    from models import Civilian

    try:
        existing = db.session.query(Civilian).filter_by(
            first_name=first_name,
            last_name=last_name
        ).first()
        return existing is not None
    except Exception as e:
        logger.warning(f'Name check failed: {e}')
        return False


def generate_ai_civilian(params):
    """Generate civilian data for form population (NO auto-save)."""

    # Try AI generation first
    try:
        ai_result = generate_character(
            params.get('age', random.randint(18, 70)),
            params.get('gender', 'random'),
            params.get('ethnicity', 'random'),
            params.get('occupation_type', 'random'),
            params.get('neighborhood', 'random'),
        )

        if 'error' not in ai_result:
            # Check for duplicate names
            attempts = 0
            while check_name_exists(ai_result.get('first_name'), ai_result.get('last_name')) and attempts < 5:
                logger.info('Duplicate name detected, regenerating...')
                ai_result = generate_character(
                    params.get('age', random.randint(18, 70)),
                    params.get('gender', 'random'),
                    params.get('ethnicity', 'random'),
                    params.get('occupation_type', 'random'),
                    params.get('neighborhood', 'random'),
                )
                attempts += 1

            return ai_result, 'ai'
    except Exception as e:
        logger.warning(f'AI generation failed, using fallback: {e}')

    # Fallback to local generator - ONLY FORM FIELDS
    name = generate_name(params.get('gender', 'random'))

    # Check for duplicates
    attempts = 0
    while check_name_exists(name['first_name'], name['last_name']) and attempts < 5:
        name = generate_name(params.get('gender', 'random'))
        attempts += 1

    address = generate_address(params.get('neighborhood'))

    return {
        'first_name': name['first_name'],
        'last_name': name['last_name'],
        'date_of_birth': (datetime.now() - timedelta(days=random.randint(18 * 365, 70 * 365))).strftime('%Y-%m-%d'),
        'gender': name['gender'],
        'phone_number': f"555-{random.randint(1000, 9999)}",
        'address': address,
        'occupation': params.get('occupation_type', 'random'),
        'gang_affiliation': 'None',
        'emergency_contact_name': generate_name()['full_name'],
        'emergency_contact_phone': f"555-{random.randint(1000, 9999)}",
        'driver_license_status': 'Valid',
        'firearm_license_status': 'None',
        'business_license_status': 'None',
        'vehicle_make': None,
        'vehicle_model': None,
        'vehicle_year': None,
        'vehicle_color': None,
        'plate_number': None,
        'insurance_status': 'Valid',
        'criminal_background_notes': 'No criminal history on file',
        'character_backstory': f"New resident of {params.get('neighborhood', 'the city')}. Just arrived looking for opportunities.",
    }, 'fallback'
