#!/usr/bin/env python
"""Update admin user role to PlatformOwner."""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def migrate_admin_role():
    """Update admin user role to PlatformOwner."""
    try:
        logger.info('=' * 80)
        logger.info('ADMIN ROLE MIGRATION')
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
                logger.info('   ✓ users table does not exist (no migration needed)')
                cursor.close()
                connection.close()
                return True
            
            logger.info('   ✓ users table exists')
            
            # 2. Check if admin user exists
            logger.info('\n2. Checking for admin user...')
            cursor.execute("""
                SELECT id, email, role
                FROM users
                WHERE email = 'admin@govdirect.org'
            """)
            
            admin_user = cursor.fetchone()
            if not admin_user:
                logger.warning('   ⚠ Admin user (admin@govdirect.org) not found')
                cursor.close()
                connection.close()
                return True
            
            user_id, email, current_role = admin_user
            logger.info(f'   ✓ Found user: {email}')
            logger.info(f'     Current role: {current_role}')
            
            # 3. Check if platform_role column exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users'
                    AND column_name = 'platform_role'
                )
            """)
            has_platform_role_col = cursor.fetchone()[0]
            
            if has_platform_role_col:
                cursor.execute("""
                    SELECT platform_role FROM users WHERE email = 'admin@govdirect.org'
                """)
                row = cursor.fetchone()
                current_platform_role = row[0] if row else None
                logger.info(f'     Current platform_role: {current_platform_role}')
            else:
                logger.info('     platform_role column not present in schema')
            
            # 4. Update admin user role
            logger.info('\n3. Updating admin user role...')
            try:
                if has_platform_role_col:
                    cursor.execute("""
                        UPDATE users
                        SET
                            role = 'PlatformOwner',
                            platform_role = 'PlatformOwner'
                        WHERE email = 'admin@govdirect.org'
                    """)
                else:
                    cursor.execute("""
                        UPDATE users
                        SET role = 'PlatformOwner'
                        WHERE email = 'admin@govdirect.org'
                    """)
                
                rows_affected = cursor.rowcount
                connection.commit()
                logger.info(f'   ✓ Updated {rows_affected} row(s)')
            except Exception as e:
                logger.error(f'   ✗ Failed to update user: {e}')
                connection.rollback()
                cursor.close()
                connection.close()
                return False
            
            # 5. Verify update
            logger.info('\n4. Verifying update...')
            cursor.execute("""
                SELECT id, email, role
                FROM users
                WHERE email = 'admin@govdirect.org'
            """)
            
            updated_user = cursor.fetchone()
            if updated_user:
                user_id, email, new_role = updated_user
                logger.info(f'   ✓ User: {email}')
                logger.info(f'     New role: {new_role}')
                
                if has_platform_role_col:
                    cursor.execute("""
                        SELECT platform_role FROM users WHERE email = 'admin@govdirect.org'
                    """)
                    row = cursor.fetchone()
                    new_platform_role = row[0] if row else None
                    logger.info(f'     New platform_role: {new_platform_role}')
                    
                    if new_role == 'PlatformOwner' and new_platform_role == 'PlatformOwner':
                        logger.info('   ✓ Update verified successfully')
                    else:
                        logger.error('   ✗ Update verification failed')
                        cursor.close()
                        connection.close()
                        return False
                else:
                    if new_role == 'PlatformOwner':
                        logger.info('   ✓ Update verified successfully')
                    else:
                        logger.error('   ✗ Update verification failed')
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
            logger.info('✓ ADMIN ROLE MIGRATION COMPLETED SUCCESSFULLY')
            logger.info('=' * 80)
            return True
            
    except Exception as e:
        logger.error(f'Migration failed: {e}', exc_info=True)
        return False

if __name__ == '__main__':
    success = migrate_admin_role()
    sys.exit(0 if success else 1)
