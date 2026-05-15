#!/usr/bin/env python
"""Migrate config table constraint from single-tenant to multi-tenant."""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_config_constraint():
    """Migrate config constraint to multi-tenant.

    Drops the legacy UNIQUE(key) constraint and replaces it with the
    multi-tenant UNIQUE(key, community_id) constraint.  Safe to run
    multiple times — exits early if the new constraint already exists.
    """
    try:
        logger.info('=' * 80)
        logger.info('CONFIG CONSTRAINT MIGRATION')
        logger.info('=' * 80)

        from server import app
        from database import db

        with app.app_context():
            connection = db.engine.raw_connection()
            cursor = connection.cursor()

            # 1. Check if config table exists
            logger.info('\n1. Checking if config table exists...')
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'config'
                )
            """)

            if not cursor.fetchone()[0]:
                logger.info('   ✓ config table does not exist (no migration needed)')
                cursor.close()
                connection.close()
                return True

            logger.info('   ✓ config table exists')

            # 2. Check for old single-tenant constraint
            logger.info('\n2. Checking for old single-tenant constraint...')
            cursor.execute("""
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'config'::regclass
                AND conname = 'config_key_key'
            """)

            old_constraint = cursor.fetchone()
            if old_constraint:
                logger.info(f'   Found: {old_constraint[0]}')
            else:
                logger.info('   ✓ Old constraint does not exist')

            # 3. Check for new multi-tenant constraint
            logger.info('\n3. Checking for new multi-tenant constraint...')
            cursor.execute("""
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'config'::regclass
                AND conname = 'uq_config_key_community'
            """)

            new_constraint = cursor.fetchone()
            if new_constraint:
                logger.info(f'   ✓ New constraint already exists: {new_constraint[0]}')
                cursor.close()
                connection.close()
                return True
            else:
                logger.info('   New constraint does not exist (will create)')

            # 4. If old constraint exists, drop it
            if old_constraint:
                logger.info('\n4. Dropping old single-tenant constraint...')
                try:
                    cursor.execute('ALTER TABLE config DROP CONSTRAINT IF EXISTS config_key_key')
                    connection.commit()
                    logger.info('   ✓ Old constraint dropped')
                except Exception as e:
                    logger.error(f'   ✗ Failed to drop constraint: {e}')
                    connection.rollback()
                    cursor.close()
                    connection.close()
                    return False

            # 5. Create new multi-tenant constraint
            logger.info('\n5. Creating new multi-tenant constraint...')
            try:
                cursor.execute("""
                    ALTER TABLE config
                    ADD CONSTRAINT uq_config_key_community
                    UNIQUE (key, community_id)
                """)
                connection.commit()
                logger.info('   ✓ New multi-tenant constraint created')
            except Exception as e:
                logger.error(f'   ✗ Failed to create constraint: {e}')
                connection.rollback()
                cursor.close()
                connection.close()
                return False

            # 6. Verify migration
            logger.info('\n6. Verifying migration...')
            cursor.execute("""
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'config'::regclass
                AND (conname = 'config_key_key' OR conname = 'uq_config_key_community')
            """)

            constraints = [row[0] for row in cursor.fetchall()]
            logger.info(f'   Current constraints: {constraints}')

            if 'config_key_key' in constraints:
                logger.error('   ✗ Old constraint still exists!')
                cursor.close()
                connection.close()
                return False

            if 'uq_config_key_community' not in constraints:
                logger.error('   ✗ New constraint not found!')
                cursor.close()
                connection.close()
                return False

            logger.info('   ✓ Migration verified successfully')

            # 7. Show sample data
            logger.info('\n7. Sample config data:')
            cursor.execute("""
                SELECT key, community_id
                FROM config
                ORDER BY key, community_id
                LIMIT 10
            """)

            rows = cursor.fetchall()
            if rows:
                for key, community_id in rows:
                    logger.info(f'   key={key}, community_id={community_id}')
            else:
                logger.info('   (no config records yet)')

            cursor.close()
            connection.close()

            logger.info('\n' + '=' * 80)
            logger.info('✓ CONFIG CONSTRAINT MIGRATION COMPLETED SUCCESSFULLY')
            logger.info('=' * 80)
            return True

    except Exception as e:
        logger.error(f'Migration failed: {e}', exc_info=True)
        return False


if __name__ == '__main__':
    success = migrate_config_constraint()
    sys.exit(0 if success else 1)
