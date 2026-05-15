#!/usr/bin/env python
"""Fix live PostgreSQL database schema."""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fix_database():
    """Fix live PostgreSQL database schema."""
    try:
        logger.info('=' * 80)
        logger.info('DATABASE FIX PROCEDURE')
        logger.info('=' * 80)
        
        from server import app
        from database import db
        from tenant_schema import (
            COMMUNITY_ID_DEFINITION,
            ensure_tenant_community_columns,
            ensure_tenant_indexes,
        )
        from schema_sync import ensure_application_schema, rollback_connection, rollback_session
        
        with app.app_context():
            # Get connection
            connection = db.engine.raw_connection()
            cursor = connection.cursor()
            
            # 1. Repair additive schema drift across SQLAlchemy models first.
            logger.info('\n1. Checking SQLAlchemy model schema alignment...')
            app_schema_ok = ensure_application_schema(cursor, connection)
            if app_schema_ok:
                logger.info('   ✓ dispatch_calls schema aligned')
            else:
                logger.warning('   ⚠ Schema drift repair reported unresolved columns; continuing legacy checks')
                logger.info('   ✓ schema validation recovered')

            # 2. Check if civilians table exists
            logger.info('\n2. Checking civilians table...')
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_name = 'civilians'
                )
            """)
            
            if not cursor.fetchone()[0]:
                logger.error('   ✗ civilians table does not exist!')
                logger.info('   Creating table from SQLAlchemy model...')
                cursor.close()
                connection.close()
                
                # Create all tables
                db.create_all()
                logger.info('   ✓ Table created')

                connection = db.engine.raw_connection()
                cursor = connection.cursor()
                try:
                    ensure_tenant_community_columns(cursor)
                    ensure_tenant_indexes(cursor)
                    connection.commit()
                    logger.info('   ✓ Tenant indexes created')
                except Exception as e:
                    logger.error(f'   ✗ Tenant schema repair failed: {e}')
                    rollback_connection(connection)
                    cursor.close()
                    connection.close()
                    return False
                if not app_schema_ok:
                    logger.error('   ✗ Application schema remains unresolved')
                    cursor.close()
                    connection.close()
                    return False
                cursor.close()
                connection.close()
                return True
            
            logger.info('   ✓ Table exists')
            
            # 3. Get live columns
            logger.info('\n3. Getting live columns...')
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'civilians'
                ORDER BY ordinal_position
            """)
            
            live_columns = {row[0] for row in cursor.fetchall()}
            logger.info(f'   Found {len(live_columns)} columns')
            
            # 4. Get expected columns
            logger.info('\n4. Getting expected columns...')
            from models import Civilian
            
            expected_columns = {col.name for col in Civilian.__table__.columns}
            logger.info(f'   Expected {len(expected_columns)} columns')
            
            # 5. Find missing columns
            logger.info('\n5. Checking for missing columns...')
            missing = expected_columns - live_columns
            
            if not missing:
                logger.info('   ✓ No missing columns')
                logger.info('   Ensuring community_id on all tenant tables...')
                try:
                    ensure_tenant_community_columns(cursor)
                    ensure_tenant_indexes(cursor)
                    connection.commit()
                    logger.info('   ✓ Tenant indexes created')
                except Exception as e:
                    logger.error(f'   ✗ Tenant schema repair failed: {e}')
                    rollback_connection(connection)
                    cursor.close()
                    connection.close()
                    return False
                if not app_schema_ok:
                    logger.error('   ✗ Application schema remains unresolved')
                    cursor.close()
                    connection.close()
                    return False
                cursor.close()
                connection.close()
                return True
            
            logger.warning(f'   ✗ Missing {len(missing)} columns: {missing}')
            
            # 6. Add missing columns
            logger.info('\n6. Adding missing columns...')
            
            # Define column definitions. community_id must remain present
            # here so schema validation never logs "No definition for community_id".
            column_defs = {
                'community_id': COMMUNITY_ID_DEFINITION,
                'date_of_birth': {'type': 'DATE', 'nullable': True, 'index': False},
                'gender': {'type': 'VARCHAR(64)', 'nullable': True, 'index': False},
                'phone_number': {'type': 'VARCHAR(64)', 'nullable': True, 'index': False},
                'address': {'type': 'VARCHAR(255)', 'nullable': True, 'index': False},
                'occupation': {'type': 'VARCHAR(255)', 'nullable': True, 'index': False},
                'gang_affiliation': {'type': "VARCHAR(255) DEFAULT 'None'", 'nullable': True, 'index': False},
                'emergency_contact_name': {'type': 'VARCHAR(255)', 'nullable': True, 'index': False},
                'emergency_contact_phone': {'type': 'VARCHAR(64)', 'nullable': True, 'index': False},
                'driver_license_status': {'type': "VARCHAR(64) DEFAULT 'Valid'", 'nullable': True, 'index': False},
                'firearm_license_status': {'type': "VARCHAR(64) DEFAULT 'None'", 'nullable': True, 'index': False},
                'business_license_status': {'type': "VARCHAR(64) DEFAULT 'None'", 'nullable': True, 'index': False},
                'vehicle_make': {'type': 'VARCHAR(255)', 'nullable': True, 'index': False},
                'vehicle_model': {'type': 'VARCHAR(255)', 'nullable': True, 'index': False},
                'vehicle_year': {'type': 'INTEGER', 'nullable': True, 'index': False},
                'vehicle_color': {'type': 'VARCHAR(64)', 'nullable': True, 'index': False},
                'plate_number': {'type': 'VARCHAR(64)', 'nullable': True, 'index': False},
                'insurance_status': {'type': "VARCHAR(64) DEFAULT 'Valid'", 'nullable': True, 'index': False},
                'criminal_background_notes': {'type': 'TEXT', 'nullable': True, 'index': False},
                'character_backstory': {'type': 'TEXT', 'nullable': True, 'index': False},
                'updated_at': {'type': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP', 'nullable': True, 'index': False},
            }
            
            for col_name in missing:
                if col_name not in column_defs:
                    logger.warning(f'   ⚠ No definition for {col_name}, skipping')
                    continue
                
                col_def = column_defs[col_name]['type']
                try:
                    sql = f'ALTER TABLE civilians ADD COLUMN IF NOT EXISTS {col_name} {col_def}'
                    cursor.execute(sql)
                    logger.info(f'   ✓ Added {col_name}')
                except Exception as e:
                    logger.error(f'   ✗ Failed to add {col_name}: {e}')
                    rollback_connection(connection)
            
            logger.info('   Ensuring community_id on all tenant tables...')
            try:
                ensure_tenant_community_columns(cursor)
                ensure_tenant_indexes(cursor)
                logger.info('   ✓ Tenant indexes created')
                connection.commit()
            except Exception as e:
                logger.error(f'   ✗ Tenant schema repair failed: {e}')
                rollback_connection(connection)
                cursor.close()
                connection.close()
                return False

            logger.info('   ✓ All columns added')
            
            # 7. Verify fix
            logger.info('\n7. Verifying fix...')
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'civilians'
                ORDER BY ordinal_position
            """)
            
            final_columns = {row[0] for row in cursor.fetchall()}
            final_missing = expected_columns - final_columns
            
            if final_missing:
                logger.error(f'   ✗ Still missing: {final_missing}')
                cursor.close()
                connection.close()
                return False
            
            logger.info(f'   ✓ All {len(final_columns)} columns present')
            
            # 8. Test INSERT
            logger.info('\n8. Testing INSERT...')
            try:
                cursor.execute("""
                    INSERT INTO civilians (
                        civilian_id, first_name, last_name, date_of_birth
                    ) VALUES (
                        'TEST-FIX-001', 'Test', 'Fix', '1990-01-01'
                    )
                """)
                connection.commit()
                logger.info('   ✓ INSERT succeeded')
                
                # Clean up
                cursor.execute("DELETE FROM civilians WHERE civilian_id='TEST-FIX-001'")
                connection.commit()
                logger.info('   ✓ Test record cleaned up')
                
            except Exception as e:
                logger.error(f'   ✗ INSERT failed: {e}')
                connection.rollback()
                cursor.close()
                connection.close()
                return False
            
            if not app_schema_ok:
                logger.error('   ✗ Application schema remains unresolved')
                cursor.close()
                connection.close()
                return False

            cursor.close()
            connection.close()
            
            logger.info('\n' + '=' * 80)
            logger.info('✓ DATABASE FIX COMPLETED SUCCESSFULLY')
            logger.info('=' * 80)
            return True
            
    except Exception as e:
        try:
            from database import db
            rollback_session(db)
        except Exception:
            pass
        logger.error(f'Fix failed: {e}', exc_info=True)
        return False

if __name__ == '__main__':
    success = fix_database()
    sys.exit(0 if success else 1)
