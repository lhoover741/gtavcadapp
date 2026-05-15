import logging
import secrets
from datetime import datetime
from database import db
from community_service import scoped_query
from models import Civilian, AuditLog, AIGenerationLog

logger = logging.getLogger(__name__)


def check_name_uniqueness(first_name, last_name):
    """Check if a name already exists in the database."""
    existing = Civilian.query.filter(
        (Civilian.first_name.ilike(first_name)) &
        (Civilian.last_name.ilike(last_name))
    ).first()
    return existing is None


def find_similar_names(first_name, last_name):
    """Find similar names using fuzzy matching."""
    from difflib import SequenceMatcher

    similar = []
    all_civilians = scoped_query(Civilian).all()

    for civ in all_civilians:
        first_ratio = SequenceMatcher(None, first_name.lower(), (civ.first_name or '').lower()).ratio()
        last_ratio = SequenceMatcher(None, last_name.lower(), (civ.last_name or '').lower()).ratio()

        if first_ratio > 0.8 or last_ratio > 0.8:
            similar.append({
                'name': f'{civ.first_name} {civ.last_name}',
                'similarity': max(first_ratio, last_ratio)
            })

    return similar


def create_civilian_from_ai(ai_data):
    """Create a civilian record from AI-generated data (form fields only)."""
    civilian_id = f"CIV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    dob = None
    raw_dob = ai_data.get('date_of_birth', '')
    if raw_dob:
        try:
            dob = datetime.strptime(raw_dob, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            dob = None

    civilian = Civilian(
        civilian_id=civilian_id,
        first_name=ai_data.get('first_name', ''),
        last_name=ai_data.get('last_name', ''),
        date_of_birth=dob,
        gender=ai_data.get('gender', ''),
        phone_number=ai_data.get('phone_number', ''),
        address=ai_data.get('address', ''),
        occupation=ai_data.get('occupation', ''),
        gang_affiliation='None',
        emergency_contact_name=ai_data.get('emergency_contact_name', ''),
        emergency_contact_phone=ai_data.get('emergency_contact_phone', ''),
        driver_license_status='Valid',
        firearm_license_status='None',
        business_license_status='None',
        vehicle_make=None,
        vehicle_model=None,
        vehicle_year=None,
        vehicle_color=None,
        plate_number=None,
        insurance_status='Valid',
        criminal_background_notes='No criminal history on file',
        character_backstory=ai_data.get('character_backstory', ai_data.get('biography', '')),
    )

    try:
        db.session.add(civilian)
        db.session.commit()
        return civilian
    except Exception as e:
        db.session.rollback()
        raise e


def log_audit(actor, action, record_type, record_id, actor_role=None, before_state=None, after_state=None, ip_address=None):
    """Create an audit log entry."""
    log_id = f"AUD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    audit = AuditLog(
        log_id=log_id,
        actor=actor,
        actor_role=actor_role,
        action=action,
        record_type=record_type,
        record_id=record_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
    )

    try:
        db.session.add(audit)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'Audit log failed: {e}')


def log_ai_generation(generation_type, input_params, output_summary, tokens_used=0, cost=0.0, status='Success', error_message=None):
    """Log AI generation for tracking and cost analysis."""
    log_id = f"AI-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"

    ai_log = AIGenerationLog(
        log_id=log_id,
        generation_type=generation_type,
        input_params=str(input_params),
        output_summary=output_summary,
        tokens_used=tokens_used,
        cost=cost,
        status=status,
        error_message=error_message,
    )

    try:
        db.session.add(ai_log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'AI log failed: {e}')
