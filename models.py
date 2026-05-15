from datetime import datetime
from database import db


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(64), default='Civilian')
    platform_role = db.Column(db.String(64), nullable=True)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    discord_id = db.Column(db.String(255), nullable=True)
    server_id = db.Column(db.String(64), nullable=True)  # For future multi-server support

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'active': self.active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'discord_id': self.discord_id,
            'server_id': self.server_id
        }


class Complaint(db.Model):
    __tablename__ = 'complaints'

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    complaint_discord = db.Column(db.String(255))
    reported_name = db.Column(db.String(255))
    complaint_type = db.Column(db.String(255))
    incident_date = db.Column(db.String(255))
    incident_location = db.Column(db.String(255))
    witnesses = db.Column(db.Text)
    evidence_link = db.Column(db.Text)
    description = db.Column(db.Text)
    resolution = db.Column(db.Text)
    status = db.Column(db.String(64), default='Open')
    staff_notes = db.Column(db.Text, default='')
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Application(db.Model):
    __tablename__ = 'applications'

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    app_discord = db.Column(db.String(255))
    app_character = db.Column(db.String(255))
    application_type = db.Column(db.String(255))
    age_confirmation = db.Column(db.String(255))
    experience = db.Column(db.Text)
    role_reason = db.Column(db.Text)
    availability = db.Column(db.Text)
    status = db.Column(db.String(64), default='Pending')
    staff_notes = db.Column(db.Text, default='')
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Civilian(db.Model):
    __tablename__ = 'civilians'

    id = db.Column(db.Integer, primary_key=True)
    civilian_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # ONLY fields visible on Civilian Registration form
    first_name = db.Column(db.String(255), nullable=False)
    last_name = db.Column(db.String(255), nullable=False)
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(64))
    phone_number = db.Column(db.String(64))
    address = db.Column(db.String(255))
    occupation = db.Column(db.String(255))
    gang_affiliation = db.Column(db.String(255), default='None')

    # Emergency contact
    emergency_contact_name = db.Column(db.String(255))
    emergency_contact_phone = db.Column(db.String(64))

    # License/status fields
    driver_license_status = db.Column(db.String(64), default='Valid')
    firearm_license_status = db.Column(db.String(64), default='None')
    business_license_status = db.Column(db.String(64), default='None')

    # Vehicle info
    vehicle_make = db.Column(db.String(255))
    vehicle_model = db.Column(db.String(255))
    vehicle_year = db.Column(db.Integer)
    vehicle_color = db.Column(db.String(64))
    plate_number = db.Column(db.String(64))
    insurance_status = db.Column(db.String(64), default='Valid')

    # Background/notes
    criminal_background_notes = db.Column(db.Text)
    character_backstory = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Return only form-visible fields."""
        return {
            'civilian_id': self.civilian_id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'date_of_birth': self.date_of_birth.isoformat() if self.date_of_birth else None,
            'gender': self.gender,
            'phone_number': self.phone_number,
            'address': self.address,
            'occupation': self.occupation,
            'gang_affiliation': self.gang_affiliation,
            'emergency_contact_name': self.emergency_contact_name,
            'emergency_contact_phone': self.emergency_contact_phone,
            'driver_license_status': self.driver_license_status,
            'firearm_license_status': self.firearm_license_status,
            'business_license_status': self.business_license_status,
            'vehicle_make': self.vehicle_make,
            'vehicle_model': self.vehicle_model,
            'vehicle_year': self.vehicle_year,
            'vehicle_color': self.vehicle_color,
            'plate_number': self.plate_number,
            'insurance_status': self.insurance_status,
            'criminal_background_notes': self.criminal_background_notes,
            'character_backstory': self.character_backstory,
        }


class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.String(64), unique=True, nullable=True)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    owner_civilian_id = db.Column(db.String(64))
    plate = db.Column(db.String(64), unique=True, nullable=False)
    vin = db.Column(db.String(255))
    make = db.Column(db.String(255))
    model = db.Column(db.String(255))
    color = db.Column(db.String(255))
    registration_status = db.Column(db.String(64), default='Valid')
    insurance_status = db.Column(db.String(64), default='Valid')
    stolen_flag = db.Column(db.Boolean, default=False)
    impound_status = db.Column(db.String(64), default='None')
    bolo_link = db.Column(db.String(64))
    notes = db.Column(db.Text)
    # Legacy field kept for backward compatibility
    owner_name = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class License(db.Model):
    __tablename__ = 'licenses'

    id = db.Column(db.Integer, primary_key=True)
    license_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    owner_name = db.Column(db.String(255))
    license_type = db.Column(db.String(255))
    status = db.Column(db.String(64), default='Valid')
    issued_date = db.Column(db.String(64))
    expiry_date = db.Column(db.String(64))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Warrant(db.Model):
    __tablename__ = 'warrants'

    id = db.Column(db.Integer, primary_key=True)
    warrant_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64))
    # Legacy arrest-warrant fields retained for backward compatibility.
    warrant_name = db.Column(db.String(255))
    warrant_charges = db.Column(db.Text)
    warrant_issuer = db.Column(db.String(255))
    warrant_notes = db.Column(db.Text)
    warrant_status = db.Column(db.String(64), default='Active')
    justification = db.Column(db.Text)
    # Expanded typed-warrant fields.
    warrant_type = db.Column(db.String(64), default='Arrest Warrant')
    warrant_number = db.Column(db.String(64), index=True)
    judge_or_authority = db.Column(db.String(255))
    issuing_agency = db.Column(db.String(255))
    subject_name = db.Column(db.String(255))
    subject_dob = db.Column(db.String(64))
    subject_address = db.Column(db.Text)
    charges_or_basis = db.Column(db.Text)
    probable_cause = db.Column(db.Text)
    search_location = db.Column(db.Text)
    items_to_seize = db.Column(db.Text)
    court_case_number = db.Column(db.String(128))
    bench_failure_reason = db.Column(db.Text)
    administrative_basis = db.Column(db.Text)
    inspection_scope = db.Column(db.Text)
    originating_jurisdiction = db.Column(db.String(255))
    extradition_location = db.Column(db.String(255))
    fugitive_last_known_location = db.Column(db.Text)
    alias_names = db.Column(db.Text)
    execution_instructions = db.Column(db.Text)
    expiration_date = db.Column(db.String(64))
    status = db.Column(db.String(64), default='Active')
    created_by_user_id = db.Column(db.Integer, nullable=True)
    approved_by_user_id = db.Column(db.Integer, nullable=True)
    pdf_attachment_id = db.Column(db.String(64), nullable=True)
    pdf_generated_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Arrest(db.Model):
    __tablename__ = 'arrests'

    id = db.Column(db.Integer, primary_key=True)
    arrest_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64))
    suspect_name = db.Column(db.String(255))
    charges = db.Column(db.Text)
    arresting_officer = db.Column(db.String(255))
    arrest_location = db.Column(db.String(255))
    evidence_attached = db.Column(db.Text)
    penalty = db.Column(db.String(255))
    report_notes = db.Column(db.Text)
    narrative = db.Column(db.Text)
    status = db.Column(db.String(64), default='Active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Incident(db.Model):
    __tablename__ = 'incidents'

    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    incident_type = db.Column(db.String(255))
    location = db.Column(db.String(255))
    description = db.Column(db.Text)
    officers_involved = db.Column(db.Text)
    suspects = db.Column(db.Text)
    status = db.Column(db.String(64), default='Open')
    priority = db.Column(db.String(64), default='Medium')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Evidence(db.Model):
    __tablename__ = 'evidence'

    id = db.Column(db.Integer, primary_key=True)
    evidence_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    case_number = db.Column(db.String(64))
    evidence_type = db.Column(db.String(128))
    evidence_description = db.Column(db.Text)
    collected_by = db.Column(db.String(255))
    officer = db.Column(db.String(255))
    clip_link = db.Column(db.Text)
    screenshot_link = db.Column(db.Text)
    storage_status = db.Column(db.String(64), default='Logged')
    chain_of_custody = db.Column(db.Text)
    location_found = db.Column(db.String(255))
    status = db.Column(db.String(64), default='Active')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class TrafficStop(db.Model):
    __tablename__ = 'traffic_stops'

    id = db.Column(db.Integer, primary_key=True)
    stop_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    driver_name = db.Column(db.String(255))
    plate = db.Column(db.String(64))
    reason = db.Column(db.Text)
    outcome = db.Column(db.String(255))
    officer = db.Column(db.String(255))
    location = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Call911(db.Model):
    __tablename__ = 'calls_911'

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    caller_name = db.Column(db.String(255))
    phone = db.Column(db.String(64))
    location = db.Column(db.Text)
    description = db.Column(db.Text)
    incident_type = db.Column(db.String(255))
    priority = db.Column(db.String(64), default='Medium')
    assigned_unit = db.Column(db.String(64))
    status = db.Column(db.String(64), default='New')
    dispatch_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class ActivityLog(db.Model):
    __tablename__ = 'activity_log'

    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    action = db.Column(db.String(255))
    officer = db.Column(db.String(255))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Bolo(db.Model):
    __tablename__ = 'bolos'

    id = db.Column(db.Integer, primary_key=True)
    bolo_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    suspect_name = db.Column(db.String(255))
    description = db.Column(db.Text)
    last_location = db.Column(db.String(255))
    vehicle = db.Column(db.String(255))
    charges = db.Column(db.Text)
    threat_level = db.Column(db.String(64), default='Medium')
    issued_by = db.Column(db.String(255))
    status = db.Column(db.String(64), default='Active')
    auto_generated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class OfficerSession(db.Model):
    __tablename__ = 'officer_sessions'

    id = db.Column(db.Integer, primary_key=True)
    callsign = db.Column(db.String(64), nullable=False)  # Unique per community
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    officer_name = db.Column(db.String(255))
    badge_number = db.Column(db.String(64))
    department = db.Column(db.String(255), default='LSPD')
    status = db.Column(db.String(64), default='On Duty')
    logged_in_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # Link to User model

    user = db.relationship('User', backref='officer_sessions')


class Config(db.Model):
    __tablename__ = 'config'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(255), nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp. Null = global config
    value = db.Column(db.Text, nullable=True)  # JSON string
    description = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Unique constraint: (key, community_id) - allows same key for different communities
    __table_args__ = (db.UniqueConstraint('key', 'community_id', name='uq_config_key_community'),)

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'value': self.value,
            'description': self.description,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Alert(db.Model):
    __tablename__ = 'alerts'

    id = db.Column(db.Integer, primary_key=True)
    alert_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    alert_type = db.Column(db.String(64))
    message = db.Column(db.Text)
    issued_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class RadioLog(db.Model):
    __tablename__ = 'radio_log'

    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    unit = db.Column(db.String(255))
    channel = db.Column(db.String(64), default='Primary')
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ServerStatus(db.Model):
    __tablename__ = 'server_status'

    id = db.Column(db.Integer, primary_key=True)
    city_status = db.Column(db.String(64), default='ACTIVE')
    player_count = db.Column(db.Integer, default=0)
    max_players = db.Column(db.Integer, default=32)
    custom_message = db.Column(db.String(255), default='24/7 dispatch channel live')
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)


class Inmate(db.Model):
    __tablename__ = 'inmates'

    id = db.Column(db.Integer, primary_key=True)
    inmate_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64), default='')
    suspect_name = db.Column(db.String(255))
    charges = db.Column(db.Text)
    penalty = db.Column(db.String(255))
    cell = db.Column(db.String(64))
    booked_by = db.Column(db.String(255))
    arrest_id = db.Column(db.String(64))
    estimated_release = db.Column(db.String(64))
    notes = db.Column(db.Text)
    status = db.Column(db.String(64), default='In Custody')
    booked_at = db.Column(db.DateTime, default=datetime.utcnow)
    released_at = db.Column(db.DateTime)
    released_by = db.Column(db.String(255))
    release_reason = db.Column(db.Text)
    updated_at = db.Column(db.DateTime)


class Hearing(db.Model):
    __tablename__ = 'hearings'

    id = db.Column(db.Integer, primary_key=True)
    hearing_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64), default='')
    suspect_name = db.Column(db.String(255))
    charges = db.Column(db.Text)
    hearing_type = db.Column(db.String(64), default='Arraignment')
    scheduled_at = db.Column(db.String(64))
    judge = db.Column(db.String(255))
    notes = db.Column(db.Text)
    arrest_id = db.Column(db.String(64))
    filing_officer = db.Column(db.String(255))
    outcome = db.Column(db.Text)
    sentence_length = db.Column(db.String(255))
    fine_amount = db.Column(db.String(255))
    outcome_notes = db.Column(db.Text)
    status = db.Column(db.String(64), default='Scheduled')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class DispatchCall(db.Model):
    __tablename__ = 'dispatch_calls'

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    caller_name = db.Column(db.String(255))
    phone = db.Column(db.String(64))
    location = db.Column(db.Text)
    description = db.Column(db.Text)
    call_type = db.Column(db.String(255))
    priority = db.Column(db.String(64), default='Normal')
    status = db.Column(db.String(64), default='Open')
    assigned_unit = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class KnownAssociate(db.Model):
    __tablename__ = 'known_associates'

    id = db.Column(db.Integer, primary_key=True)
    associate_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64), nullable=False)
    associated_civilian_id = db.Column(db.String(64), nullable=False)
    relationship_type = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Business(db.Model):
    __tablename__ = 'businesses'

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    owner_civilian_id = db.Column(db.String(64))
    business_name = db.Column(db.String(255), nullable=False)
    business_type = db.Column(db.String(255))
    license_status = db.Column(db.String(64), default='Active')
    address = db.Column(db.Text)
    employees = db.Column(db.Integer, default=0)
    inspection_notes = db.Column(db.Text)
    legal_flags = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class Citation(db.Model):
    __tablename__ = 'citations'

    id = db.Column(db.Integer, primary_key=True)
    citation_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64), nullable=False)
    issuing_officer = db.Column(db.String(255))
    violation = db.Column(db.String(255))
    location = db.Column(db.String(255))
    fine_amount = db.Column(db.Float)
    status = db.Column(db.String(64), default='Issued')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class JailBooking(db.Model):
    __tablename__ = 'jail_bookings'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    civilian_id = db.Column(db.String(64), nullable=False, default='')
    arrest_id = db.Column(db.String(64))
    suspect_name = db.Column(db.String(255))
    charges = db.Column(db.Text)
    booking_officer = db.Column(db.String(255))
    cell_assignment = db.Column(db.String(64))
    bond_amount = db.Column(db.Float)
    sentence_length = db.Column(db.String(255))
    status = db.Column(db.String(64), default='Booked')
    release_date = db.Column(db.DateTime)
    released_by = db.Column(db.String(255))
    release_reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class UseOfForceReport(db.Model):
    __tablename__ = 'use_of_force_reports'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    officer_name = db.Column(db.String(255))
    badge_number = db.Column(db.String(64))
    subject_name = db.Column(db.String(255))
    location = db.Column(db.String(255))
    force_type = db.Column(db.String(255))
    weapon_observed = db.Column(db.String(255))
    injuries_observed = db.Column(db.Text)
    charges = db.Column(db.Text)
    narrative = db.Column(db.Text)
    supervisor_review = db.Column(db.Text)
    status = db.Column(db.String(64), default='Pending')
    ai_generated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class OfficerNote(db.Model):
    __tablename__ = 'officer_notes'

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    officer_name = db.Column(db.String(255))
    civilian_id = db.Column(db.String(64))
    note_type = db.Column(db.String(64))
    content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class CaseFile(db.Model):
    __tablename__ = 'case_files'

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.String(64), unique=True, nullable=False)
    case_number = db.Column(db.String(64), unique=True)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    title = db.Column(db.String(255))
    case_type = db.Column(db.String(64), default='incident')
    priority = db.Column(db.String(64), default='medium')
    location = db.Column(db.Text)
    involved_civilians = db.Column(db.Text)
    involved_officers = db.Column(db.Text)
    linked_911_call_id = db.Column(db.String(64))
    linked_arrest_id = db.Column(db.String(64))
    linked_warrant_id = db.Column(db.String(64))
    linked_evidence_ids = db.Column(db.Text)
    report_notes = db.Column(db.Text)
    created_by = db.Column(db.String(255))
    assigned_to = db.Column(db.String(255))
    defendant_civilian_id = db.Column(db.String(64))
    charges = db.Column(db.Text)
    evidence_ids = db.Column(db.Text)
    arrest_id = db.Column(db.String(64))
    assigned_judge = db.Column(db.String(255))
    prosecutor_notes = db.Column(db.Text)
    defense_notes = db.Column(db.Text)
    court_date = db.Column(db.DateTime)
    status = db.Column(db.String(64), default='open')
    outcome = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class CaseCharge(db.Model):
    __tablename__ = 'case_charges'

    id = db.Column(db.Integer, primary_key=True)
    charge_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=False)
    case_id = db.Column(db.String(64), nullable=False)
    charge_name = db.Column(db.String(255), nullable=False)
    penal_code = db.Column(db.String(64))
    severity = db.Column(db.String(64), default='misdemeanor')
    counts = db.Column(db.Integer, default=1)
    recommended_fine = db.Column(db.String(64))
    recommended_jail_time = db.Column(db.String(64))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class CadAuditLog(db.Model):
    __tablename__ = 'cad_audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.String(64), unique=True, nullable=False)
    acting_user_id = db.Column(db.Integer, nullable=True)
    community_id = db.Column(db.String(64), nullable=False)
    case_id = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(128), nullable=False)
    request_id = db.Column(db.String(64))
    ip_address = db.Column(db.String(64))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EvidenceAttachment(db.Model):
    __tablename__ = 'evidence_attachments'

    id = db.Column(db.Integer, primary_key=True)
    attachment_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=False, index=True)
    case_id = db.Column(db.String(64), nullable=True, index=True)
    evidence_id = db.Column(db.String(64), nullable=True, index=True)
    arrest_id = db.Column(db.String(64), nullable=True, index=True)
    warrant_id = db.Column(db.String(64), nullable=True, index=True)
    court_packet_id = db.Column(db.String(64), nullable=True, index=True)
    uploaded_by_user_id = db.Column(db.Integer, nullable=False)
    original_filename = db.Column(db.String(255), nullable=True)
    stored_filename = db.Column(db.String(255), nullable=True)
    file_type = db.Column(db.String(64), nullable=True)
    mime_type = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    storage_mode = db.Column(db.String(32), nullable=False)
    storage_path = db.Column(db.Text, nullable=True)
    external_url = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(128), nullable=True)
    review_status = db.Column(db.String(64), default='submitted')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)



class AIGenerationLog(db.Model):
    __tablename__ = 'ai_generation_logs'

    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    generation_type = db.Column(db.String(64))
    input_params = db.Column(db.Text)
    output_summary = db.Column(db.Text)
    tokens_used = db.Column(db.Integer)
    cost = db.Column(db.Float)
    status = db.Column(db.String(64), default='Success')
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), nullable=True)  # Will be backfilled with nthacityrp
    actor = db.Column(db.String(255))  # officer_name or user_id
    actor_role = db.Column(db.String(64))  # role of the actor
    action = db.Column(db.String(255))
    record_type = db.Column(db.String(64))
    record_id = db.Column(db.String(64))
    before_state = db.Column(db.Text)
    after_state = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Community(db.Model):
    """Represents a multi-tenant community within the GTAVCAD platform."""
    __tablename__ = 'communities'

    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.String(64), unique=True, nullable=False)  # e.g., 'nthacityrp', 'metro-rp'
    name = db.Column(db.String(255), nullable=False)  # Display name: NThaCityRP, Metro RP
    slug = db.Column(db.String(64), unique=True, nullable=False)  # URL-safe slug: nthacityrp, metro-rp
    cad_name = db.Column(db.String(255), nullable=False, default='Community CAD')  # e.g., 'NThaCityRP CAD'
    owner_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    logo_url = db.Column(db.String(512), nullable=True)
    primary_color = db.Column(db.String(32), default='#1a1a1a')  # Hex color
    secondary_color = db.Column(db.String(32), default='#0066cc')  # Hex color
    status = db.Column(db.String(64), default='Active')  # Active, Inactive, Suspended
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = db.relationship('User', backref=db.backref('owned_communities', lazy=True))
    members = db.relationship('CommunityMember', backref='community', lazy=True, cascade='all, delete-orphan')
    invites = db.relationship('CommunityInvite', backref='community', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'community_id': self.community_id,
            'name': self.name,
            'slug': self.slug,
            'cad_name': self.cad_name,
            'owner_user_id': self.owner_user_id,
            'logo_url': self.logo_url,
            'primary_color': self.primary_color,
            'secondary_color': self.secondary_color,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class CommunityMember(db.Model):
    """Represents a user's membership in a community with their role and permissions."""
    __tablename__ = 'community_members'

    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.String(64), db.ForeignKey('communities.community_id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(64), nullable=False)  # Owner, Admin, Police, Dispatch, Judge, DMV, Civilian, BusinessOwner
    department = db.Column(db.String(255), nullable=True)  # LSPD, BCSO, Dispatch, etc.
    callsign = db.Column(db.String(64), nullable=True)  # For officers: 1L-01, 2L-12, etc.
    status = db.Column(db.String(64), default='Active')  # Active, Inactive, Suspended
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref=db.backref('community_memberships', lazy=True))

    # Ensure a user can't have duplicate roles in the same community
    __table_args__ = (db.UniqueConstraint('community_id', 'user_id', name='unique_user_per_community'),)

    def to_dict(self):
        return {
            'id': self.id,
            'community_id': self.community_id,
            'user_id': self.user_id,
            'role': self.role,
            'department': self.department,
            'callsign': self.callsign,
            'status': self.status,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class CommunityInvite(db.Model):
    """Represents an invitation code to join a community."""
    __tablename__ = 'community_invites'

    id = db.Column(db.Integer, primary_key=True)
    invite_code = db.Column(db.String(64), unique=True, nullable=False)
    community_id = db.Column(db.String(64), db.ForeignKey('communities.community_id'), nullable=False)
    role = db.Column(db.String(64), nullable=False, default='Civilian')  # Default role for invitees
    department = db.Column(db.String(255), nullable=True)  # Optional: pre-assign department
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)  # None = never expires
    max_uses = db.Column(db.Integer, nullable=True)  # None = unlimited uses
    uses = db.Column(db.Integer, default=0)  # Current number of uses
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    creator = db.relationship('User', backref=db.backref('created_invites', lazy=True))

    def is_valid(self):
        """Check if the invite is still valid."""
        if not self.active:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        if self.max_uses is not None and self.uses >= self.max_uses:
            return False
        return True

    def to_dict(self):
        uses_remaining = None if self.max_uses is None else max(0, int(self.max_uses or 0) - int(self.uses or 0))
        invite_link = f'https://gtavcad.app/join?code={self.invite_code}'
        return {
            'id': self.id,
            'invite_code': self.invite_code,
            'code': self.invite_code,
            'invite_link': invite_link,
            'community_id': self.community_id,
            'role': self.role,
            'department': self.department,
            'created_by': self.created_by,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'max_uses': self.max_uses,
            'uses': self.uses,
            'uses_remaining': uses_remaining,
            'active': self.active,
            'revoked': not self.active,
            'valid': self.is_valid(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PlatformAdminLog(db.Model):
    __tablename__ = 'platform_admin_logs'
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    tenant = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(255), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PlatformActivityLog(db.Model):
    __tablename__ = 'platform_activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    tenant = db.Column(db.String(64), nullable=True)
    actor_username = db.Column(db.String(255), nullable=True)
    activity_type = db.Column(db.String(128), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(32), default='info')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(128), unique=True, nullable=False)
    tenant = db.Column(db.String(64), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CommunityStatus(db.Model):
    __tablename__ = 'community_status'
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.String(64), db.ForeignKey('communities.community_id'), unique=True, nullable=False)
    last_api_activity = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)
    last_cad_action = db.Column(db.DateTime, nullable=True)
    last_officer_activity = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserSession(db.Model):
    __tablename__ = 'user_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    session_token = db.Column(db.String(128), unique=True, nullable=False)
    tenant = db.Column(db.String(64), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    invalidated_at = db.Column(db.DateTime, nullable=True)
