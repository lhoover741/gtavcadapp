#!/usr/bin/env python
"""Ensure PlatformOwner role exists without mutating existing passwords."""

import os
import sys
import logging
from werkzeug.security import generate_password_hash

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def _env_true(value):
    """Return True when an environment flag is set to a truthy value."""
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def migrate_admin_password():
    """Backward-compatible wrapper for ensure_platform_owner migration."""
    return ensure_platform_owner()


def ensure_platform_owner():
    """Promote/create configured PlatformOwner and only initialize password when allowed."""
    try:
        logger.info('=' * 80)
        logger.info('PLATFORM OWNER ENSURE MIGRATION')
        logger.info('=' * 80)

        from server import app
        from database import db

        with app.app_context():
            connection = db.engine.raw_connection()
            cursor = connection.cursor()

            # 1. Check if users table exists
            logger.info('\n1. Checking if users table exists...')
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'users'
                )
            """)

            if not cursor.fetchone()[0]:
                logger.error('   ✗ users table does not exist')
                cursor.close()
                connection.close()
                return False

            logger.info('   ✓ users table exists')

            platform_owner_email = (os.getenv('PLATFORM_OWNER_EMAIL') or 'admin@govdirect.org').strip().lower()
            platform_owner_username = (os.getenv('PLATFORM_OWNER_USERNAME') or 'platformowner').strip()
            initial_password = os.getenv('PLATFORM_OWNER_INITIAL_PASSWORD')
            force_reset = _env_true(os.environ.get('FORCE_ADMIN_PASSWORD_RESET', 'false'))

            # 2. Check if configured PlatformOwner exists
            logger.info('\n2. Checking for configured PlatformOwner...')
            cursor.execute("""
                SELECT id, email, password_hash, role, platform_role, active
                FROM users
                WHERE LOWER(email) = %s
            """, (platform_owner_email,))

            admin_user = cursor.fetchone()
            if not admin_user:
                logger.warning('   ! PlatformOwner user not found for email=%s, creating one', platform_owner_email)
                password_hash = generate_password_hash(initial_password, method='pbkdf2:sha256') if initial_password else None
                cursor.execute("""
                    INSERT INTO users (username, email, password_hash, role, platform_role, active)
                    VALUES (%s, %s, %s, 'PlatformOwner', 'PlatformOwner', true)
                    RETURNING id, email, password_hash, role, platform_role, active
                """, (platform_owner_username, platform_owner_email, password_hash))
                admin_user = cursor.fetchone()

            user_id, email, current_hash, current_role, current_platform_role, current_active = admin_user
            logger.info(f'   ✓ Found user: {email}')
            logger.info(f'     Current role: {current_role}')
            logger.info(f'     Current platform_role: {current_platform_role}')
            logger.info(f'     Current active: {current_active}')
            if current_hash:
                logger.info('     Current hash_present=%s', bool(current_hash))
            else:
                logger.info('     Current hash_present=%s', False)

            # 3. Update role/status and password only when allowed
            logger.info('\n3. Ensuring PlatformOwner role/status...')
            should_set_password = (not current_hash) or force_reset

            try:
                if should_set_password:
                    if not initial_password:
                        logger.warning('   ! Password initialization requested but PLATFORM_OWNER_INITIAL_PASSWORD not set; preserving current value')
                        cursor.execute("""
                            UPDATE users
                            SET role = 'PlatformOwner', platform_role = 'PlatformOwner', active = true
                            WHERE LOWER(email) = %s
                        """, (platform_owner_email,))
                    else:
                        new_password_hash = generate_password_hash(initial_password, method='pbkdf2:sha256')
                        cursor.execute("""
                            UPDATE users
                            SET
                                password_hash = %s,
                                role = 'PlatformOwner',
                                platform_role = 'PlatformOwner',
                                active = true
                            WHERE LOWER(email) = %s
                        """, (new_password_hash, platform_owner_email))
                        logger.info('   ✓ PlatformOwner password initialized/reset by policy')
                else:
                    cursor.execute("""
                        UPDATE users
                        SET
                            role = 'PlatformOwner',
                            platform_role = 'PlatformOwner',
                            active = true
                        WHERE LOWER(email) = %s
                    """, (platform_owner_email,))
                    logger.info('   ✓ Existing PlatformOwner password preserved')

                rows_affected = cursor.rowcount
                connection.commit()
                logger.info(f'   ✓ Updated {rows_affected} row(s)')
            except Exception as e:
                logger.error(f'   ✗ Failed to update user: {e}')
                connection.rollback()
                cursor.close()
                connection.close()
                return False

            # 4. Verify update
            logger.info('\n4. Verifying update...')
            cursor.execute("""
                SELECT id, email, password_hash, role, platform_role, active
                FROM users
                WHERE LOWER(email) = %s
            """, (platform_owner_email,))

            updated_user = cursor.fetchone()
            if updated_user:
                user_id, email, new_hash, new_role, new_platform_role, new_active = updated_user
                logger.info(f'   ✓ User: {email}')
                logger.info(f'     New role: {new_role}')
                logger.info(f'     New platform_role: {new_platform_role}')
                logger.info(f'     New active: {new_active}')
                if new_hash:
                    logger.info('     password_initialized=%s', bool(new_hash))
                else:
                    logger.info('     password_initialized=%s', False)

                # Verify all fields are correct
                if (new_role == 'PlatformOwner' and
                        new_platform_role == 'PlatformOwner' and
                        new_active == True):
                    logger.info('   ✓ Update verified successfully')
                else:
                    logger.error('   ✗ Update verification failed - fields do not match expected values')
                    cursor.close()
                    connection.close()
                    return False
            else:
                logger.error('   ✗ User not found after update')
                cursor.close()
                connection.close()
                return False

            cursor.close()
            connection.close()

            logger.info('\n' + '=' * 80)
            logger.info('✓ PLATFORM OWNER ENSURE MIGRATION COMPLETED SUCCESSFULLY')
            logger.info('=' * 80)
            logger.info('\nPlatformOwner can now login with:')
            logger.info(f'  Email: {platform_owner_email}')
            logger.info('  Role: PlatformOwner')
            logger.info('  Status: Active')
            return True

    except Exception as e:
        logger.error(f'Migration failed: {e}', exc_info=True)
        return False

if __name__ == '__main__':
    success = migrate_admin_password()
    sys.exit(0 if success else 1)
