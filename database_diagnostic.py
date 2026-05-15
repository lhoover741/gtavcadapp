#!/usr/bin/env python
"""Diagnose and fix live PostgreSQL database schema."""

import os
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def diagnose_database():
    """Diagnose live PostgreSQL database."""
    try:
        logger.info('=' * 80)
        logger.info('DATABASE DIAGNOSTIC REPORT')
        logger.info('=' * 80)
        
        from server import app
        from database import db
        
        with app.app_context():
            # Get database URL (redacted to avoid leaking credentials)
            db_url = app.config.get('SQLALCHEMY_DATABASE_URI', 'NOT SET')
            logger.info('Database URL: configured' if db_url and db_url != 'NOT SET' else 'Database URL: NOT SET')
            
            # Get connection
            connection = db.engine.raw_connection()
            cursor = connection.cursor()
            
            # 1. Check if civilians table exists
            logger.info('\n1. Checking if civilians table exists...')
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_name = 'civilians'
                )
            """)
            table_exists = cursor.fetchone()[0]
            logger.info(f'   Table exists: {table_exists}')
            
            if not table_exists:
                logger.error('   ✗ civilians table does NOT exist!')
                cursor.close()
                connection.close()
                return False
            
            # 2. Get all columns in civilians table
            logger.info('\n2. Columns in live civilians table:')
            cursor.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'civilians'
                ORDER BY ordinal_position
            """)
            
            live_columns = {}
            for row in cursor.fetchall():
                col_name, col_type, is_nullable, col_default = row
                live_columns[col_name] = {
                    'type': col_type,
                    'nullable': is_nullable,
                    'default': col_default
                }
                logger.info(f'   ✓ {col_name}: {col_type} (nullable={is_nullable})')
            
            logger.info(f'\n   Total columns: {len(live_columns)}')
            
            # 3. Get expected columns from SQLAlchemy model
            logger.info('\n3. Expected columns from SQLAlchemy model:')
            from models import Civilian
            
            expected_columns = {}
            for col in Civilian.__table__.columns:
                expected_columns[col.name] = {
                    'type': str(col.type),
                    'nullable': col.nullable,
                    'default': col.default
                }
                logger.info(f'   ✓ {col.name}: {col.type}')
            
            logger.info(f'\n   Total columns: {len(expected_columns)}')
            
            # 4. Compare
            logger.info('\n4. Schema comparison:')
            missing = set(expected_columns.keys()) - set(live_columns.keys())
            extra = set(live_columns.keys()) - set(expected_columns.keys())
            
            if missing:
                logger.error(f'   ✗ MISSING columns: {missing}')
            else:
                logger.info('   ✓ No missing columns')
            
            if extra:
                logger.warning(f'   ⚠ Extra columns (safe to ignore): {extra}')
            else:
                logger.info('   ✓ No extra columns')
            
            # 5. Test manual INSERT
            logger.info('\n5. Testing manual INSERT...')
            try:
                cursor.execute("""
                    INSERT INTO civilians (
                        civilian_id,
                        first_name,
                        last_name,
                        date_of_birth,
                        gender,
                        phone_number,
                        address,
                        occupation,
                        gang_affiliation,
                        emergency_contact_name,
                        emergency_contact_phone,
                        driver_license_status,
                        firearm_license_status,
                        business_license_status,
                        vehicle_make,
                        vehicle_model,
                        vehicle_year,
                        vehicle_color,
                        plate_number,
                        insurance_status,
                        criminal_background_notes,
                        character_backstory
                    ) VALUES (
                        'TEST-DIAGNOSTIC-001',
                        'Test',
                        'Civilian',
                        '1990-01-01',
                        'Male',
                        '555-1234',
                        '123 Test St',
                        'Test Job',
                        'None',
                        'Test Contact',
                        '555-5678',
                        'Valid',
                        'None',
                        'None',
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        'Valid',
                        'No criminal history',
                        'Test backstory'
                    )
                """)
                connection.commit()
                logger.info('   ✓ Manual INSERT succeeded')
                
                # Verify insert
                cursor.execute("SELECT COUNT(*) FROM civilians WHERE civilian_id='TEST-DIAGNOSTIC-001'")
                count = cursor.fetchone()[0]
                logger.info(f'   ✓ Record persisted: {count} record(s) found')
                
                # Clean up test record
                cursor.execute("DELETE FROM civilians WHERE civilian_id='TEST-DIAGNOSTIC-001'")
                connection.commit()
                logger.info('   ✓ Test record cleaned up')
                
            except Exception as e:
                logger.error(f'   ✗ Manual INSERT failed: {e}')
                connection.rollback()
                cursor.close()
                connection.close()
                return False
            
            # 6. Summary
            logger.info('\n' + '=' * 80)
            if missing:
                logger.error('DIAGNOSIS: Schema mismatch - missing columns')
                logger.info('=' * 80)
                cursor.close()
                connection.close()
                return False
            else:
                logger.info('DIAGNOSIS: Schema is correct - all columns present')
                logger.info('=' * 80)
                cursor.close()
                connection.close()
                return True
            
    except Exception as e:
        logger.error(f'Diagnostic failed: {e}', exc_info=True)
        return False

if __name__ == '__main__':
    success = diagnose_database()
    sys.exit(0 if success else 1)
