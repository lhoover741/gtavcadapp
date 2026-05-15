#!/usr/bin/env python3
"""
GTAVCAD Multi-Tenant Bootstrap

Initializes multi-tenant system:
1. Creates the default migrated tenant community
2. Backfills all existing records with the default tenant community_id
3. Initializes community-scoped config

CRITICAL: Run this ONCE during multi-tenant migration.
"""

import os
import logging
import uuid
from datetime import datetime
from flask import Flask
from database import db
from platform_config import (
    DEFAULT_COMMUNITY_NAME,
    DEFAULT_COMMUNITY_SLUG,
    DEFAULT_COMMUNITY_CAD_NAME,
    DEFAULT_COMMUNITY_DEPARTMENTS,
)
from models import (
    Community, CommunityMember, User, Civilian, Warrant, Arrest,
    Incident, Evidence, TrafficStop, Call911, ActivityLog, Bolo,
    OfficerSession, Config, Alert, RadioLog, Inmate, Hearing,
    DispatchCall, KnownAssociate, Business, Citation, JailBooking,
    UseOfForceReport, OfficerNote, CaseFile, AIGenerationLog, AuditLog,
    Vehicle, License, Application, Complaint,
)
from tenant_schema import (
    DEFAULT_COMMUNITY_ID,
    backfill_default_community,
    ensure_tenant_community_columns,
    ensure_tenant_indexes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app():
    """Create Flask app for database access."""
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', '')
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
        app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace(
            'postgres://', 'postgresql://', 1
        )
    db.init_app(app)
    return app


def generate_id(prefix: str) -> str:
    """Generate unique ID with prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def create_default_community(session):
    """Create the migrated default tenant community."""
    logger.info(f'🏢 Creating default {DEFAULT_COMMUNITY_NAME} tenant community...')

    # Check if it already exists
    existing = Community.query.filter_by(slug=DEFAULT_COMMUNITY_SLUG).first()
    if existing:
        logger.info(f'✓ Default {DEFAULT_COMMUNITY_SLUG} tenant verified')
        return existing

    # Create first admin user if none exists
    admin_user = User.query.filter_by(role='Admin', active=True).first()
    if not admin_user:
        logger.warning('⚠️  No admin user found. Creating default admin user (username: admin, password: changeme).')
        from security_service import hash_password
        admin_user = User(
            username='admin',
            password_hash=hash_password('changeme'),
            role='Admin',
            active=True,
        )
        session.add(admin_user)
        session.commit()

    # Create Community
    community = Community(
        community_id=DEFAULT_COMMUNITY_ID,
        name=DEFAULT_COMMUNITY_NAME,
        slug=DEFAULT_COMMUNITY_SLUG,
        cad_name=DEFAULT_COMMUNITY_CAD_NAME,
        owner_user_id=admin_user.id,
        logo_url=None,
        primary_color='#1a1a1a',
        secondary_color='#0066cc',
        status='Active',
    )
    session.add(community)
    session.commit()

    logger.info(f'✅ Created default tenant: {DEFAULT_COMMUNITY_NAME} ({DEFAULT_COMMUNITY_SLUG})')

    # Create CommunityMember for admin
    admin_member = CommunityMember(
        community_id=DEFAULT_COMMUNITY_ID,
        user_id=admin_user.id,
        role='Owner',
        department='Admin',
        callsign=None,
        status='Active',
    )
    session.add(admin_member)
    session.commit()

    logger.info(f'✅ Added admin user to community as Owner')

    return community


def backfill_community_ids(session):
    """Backfill legacy records into the migrated default tenant."""
    logger.info('🔄 Backfilling community_id for existing records...')

    tables_to_backfill = [
        ('Civilian', Civilian, 'civilians'),
        ('Warrant', Warrant, 'warrants'),
        ('Arrest', Arrest, 'arrests'),
        ('Incident', Incident, 'incidents'),
        ('Evidence', Evidence, 'evidence'),
        ('TrafficStop', TrafficStop, 'traffic_stops'),
        ('Call911', Call911, 'calls_911'),
        ('ActivityLog', ActivityLog, 'activity_log'),
        ('Bolo', Bolo, 'bolos'),
        ('OfficerSession', OfficerSession, 'officer_sessions'),
        ('Alert', Alert, 'alerts'),
        ('RadioLog', RadioLog, 'radio_log'),
        ('Inmate', Inmate, 'inmates'),
        ('Hearing', Hearing, 'hearings'),
        ('DispatchCall', DispatchCall, 'dispatch_calls'),
        ('KnownAssociate', KnownAssociate, 'known_associates'),
        ('Business', Business, 'businesses'),
        ('Citation', Citation, 'citations'),
        ('JailBooking', JailBooking, 'jail_bookings'),
        ('UseOfForceReport', UseOfForceReport, 'use_of_force_reports'),
        ('OfficerNote', OfficerNote, 'officer_notes'),
        ('CaseFile', CaseFile, 'case_files'),
        ('AIGenerationLog', AIGenerationLog, 'ai_generation_logs'),
        ('AuditLog', AuditLog, 'audit_logs'),
        ('Vehicle', Vehicle, 'vehicles'),
        ('License', License, 'licenses'),
        ('Application', Application, 'applications'),
        ('Complaint', Complaint, 'complaints'),
    ]

    for model_name, model_class, table_name in tables_to_backfill:
        try:
            # Count records without community_id
            count = session.query(model_class).filter(
                model_class.community_id == None
            ).count()

            if count > 0:
                logger.info(f'  Backfilling {count} {model_name} records...')
                session.query(model_class).filter(
                    model_class.community_id == None
                ).update(
                    {model_class.community_id: DEFAULT_COMMUNITY_ID},
                    synchronize_session=False
                )
                session.commit()
                logger.info(f'  ✅ Backfilled {count} {model_name} records')
            else:
                logger.info(f'  ✓ No backfill needed for {model_name}')
        except Exception as e:
            logger.error(f'  ❌ Error backfilling {model_name}: {e}')
            session.rollback()

    logger.info('✓ community_id backfill complete')


def initialize_default_config(session, community_id=DEFAULT_COMMUNITY_ID):
    """Initialize default config for community."""
    logger.info(f'⚙️  Initializing config for community {community_id}...')

    import json

    defaults = {
        'server_name': {
            'value': DEFAULT_COMMUNITY_NAME,
            'description': 'Name of the RP server'
        },
        'cad_name': {
            'value': DEFAULT_COMMUNITY_CAD_NAME,
            'description': 'Name of the CAD system'
        },
        'departments': {
            'value': json.dumps(DEFAULT_COMMUNITY_DEPARTMENTS),
            'description': 'Available police departments'
        },
        'officer_ranks': {
            'value': json.dumps(['Officer', 'Sergeant', 'Lieutenant', 'Captain', 'Chief']),
            'description': 'Available officer ranks'
        },
        'penal_codes': {
            'value': json.dumps({
                '1.01': 'Reckless Driving',
                '1.02': 'Speeding',
                '2.01': 'Assault',
                '2.02': 'Battery',
                '3.01': 'Theft',
                '3.02': 'Burglary'
            }),
            'description': 'Penal code definitions'
        },
        'call_types': {
            'value': json.dumps(['Emergency', 'Non-Emergency', 'Traffic', 'Medical', 'Fire']),
            'description': 'Available call types'
        },
        'vehicle_categories': {
            'value': json.dumps(['Sedan', 'SUV', 'Truck', 'Motorcycle', 'Commercial']),
            'description': 'Vehicle categories'
        },
        'evidence_categories': {
            'value': json.dumps(['Physical', 'Digital', 'Witness', 'Surveillance']),
            'description': 'Evidence categories'
        },
        'business_categories': {
            'value': json.dumps(['Restaurant', 'Store', 'Bar', 'Office', 'Manufacturing', 'Other']),
            'description': 'Business categories'
        },
    }

    for key, config_data in defaults.items():
        try:
            # Check if config already exists for this community
            existing = session.query(Config).filter_by(
                key=key,
                community_id=community_id
            ).first()

            if not existing:
                config = Config(
                    key=key,
                    community_id=community_id,
                    value=config_data.get('value'),
                    description=config_data.get('description'),
                )
                session.add(config)
                logger.info(f'  Created config: {key}')
            else:
                logger.info(f'  Config already exists: {key}')
        except Exception as e:
            logger.warning(f'  Config {key} skipped (already exists): {e}')
            session.rollback()
            continue

    session.commit()
    logger.info('✅ Config initialization complete')


def main():
    """Main bootstrap flow."""
    app = create_app()

    with app.app_context():
        logger.info('=' * 60)
        logger.info('🚀 GTAVCAD Multi-Tenant Bootstrap Starting')
        logger.info('=' * 60)

        try:
            # 1. Create tables if they don't exist, including Community.
            logger.info('📋 Creating database tables...')
            db.create_all()
            logger.info('✓ Community table verified')
            logger.info('✅ Database tables ensured')

            # 2. Create default community.
            create_default_community(db.session)
            logger.info(f'✓ Default {DEFAULT_COMMUNITY_SLUG} tenant verified')

            connection = db.engine.raw_connection()
            cursor = connection.cursor()
            try:
                # 3. Add community_id columns before any ORM backfill touches them.
                ensure_tenant_community_columns(cursor)
                connection.commit()

                # 4. Backfill existing production data only where community_id IS NULL.
                backfill_default_community(cursor)
                connection.commit()
                logger.info('✓ community_id backfill complete')

                # 5. Create indexes for tenant-scoped lookups.
                ensure_tenant_indexes(cursor)
                connection.commit()
                logger.info('✓ Tenant indexes created')
            finally:
                cursor.close()
                connection.close()

            # 6. Initialize config after config.community_id is guaranteed.
            initialize_default_config(db.session, DEFAULT_COMMUNITY_ID)

            # 7. Run tenant validation.
            from tenant_isolation_validator import run_all_tests
            if not run_all_tests():
                logger.error('❌ Tenant validation failed')
                return False

            logger.info('')
            logger.info('=' * 60)
            logger.info('✅ GTAVCAD Multi-Tenant Bootstrap Complete!')
            logger.info('=' * 60)
            logger.info('')
            logger.info('Summary:')
            logger.info(f'  ✓ Default tenant: {DEFAULT_COMMUNITY_NAME}')
            logger.info(f'  ✓ Backfilled all records with community_id={DEFAULT_COMMUNITY_ID}')
            logger.info('  ✓ Initialized community config')
            logger.info('')
            logger.info('Next Steps:')
            logger.info('  1. Deploy tenancy middleware to scope all queries')
            logger.info('  2. Update RBAC decorators for community scoping')
            logger.info('  3. Test cross-community isolation')
            logger.info('  4. Enable MULTI_TENANT_ENABLED=true when ready')
            logger.info('')

        except Exception as e:
            logger.error('❌ Bootstrap failed!')
            logger.error(f'Error: {e}')
            import traceback
            traceback.print_exc()
            return False

    return True


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)
