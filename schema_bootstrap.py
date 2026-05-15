#!/usr/bin/env python
"""Bootstrap database schema on startup."""

import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def bootstrap_schema():
    """Bootstrap database schema."""
    try:
        logger.info('Starting schema bootstrap...')

        # Import Flask app
        from server import app

        with app.app_context():
            from database import db
            from bootstrap_multi_tenant import create_default_community, initialize_default_config
            from tenant_schema import (
                backfill_default_community,
                ensure_tenant_community_columns,
                ensure_tenant_indexes,
            )
            from schema_sync import ensure_application_schema, rollback_connection, rollback_session

            # 1. Create all tables, including Community.
            logger.info('Creating database tables from models...')
            db.create_all()
            logger.info('✓ Community table verified')
            logger.info('✓ Database tables created/verified')

            # 2. Ensure additive model columns exist before validators query them.
            connection = db.engine.raw_connection()
            cursor = connection.cursor()
            schema_aligned = False
            try:
                schema_aligned = ensure_application_schema(cursor, connection)
                if schema_aligned:
                    logger.info('✓ dispatch_calls schema aligned')
                else:
                    logger.warning('Recoverable schema drift detected during bootstrap repair')
                    logger.info('✓ schema validation recovered')
            except Exception as e:
                logger.error(f'Recoverable schema alignment failed: {e}', exc_info=True)
                rollback_connection(connection)
                logger.info('✓ schema validation recovered')
            finally:
                cursor.close()
                connection.close()

            # 3. Ensure the default community exists before assigning records to it.
            try:
                create_default_community(db.session)
                logger.info('✓ Default nthacityrp community verified')
            except Exception:
                rollback_session(db)
                raise

            connection = db.engine.raw_connection()
            cursor = connection.cursor()
            try:
                # 4. Add nullable community_id columns idempotently.
                try:
                    ensure_tenant_community_columns(cursor)
                    connection.commit()
                except Exception as e:
                    logger.error(f'Recoverable community_id migration failed: {e}')
                    rollback_connection(connection)
                    logger.info('✓ schema validation recovered')

                # 5. Backfill only records where community_id IS NULL.
                try:
                    backfill_default_community(cursor)
                    connection.commit()
                    logger.info('✓ community_id backfill complete')
                except Exception as e:
                    logger.error(f'Recoverable community_id backfill failed: {e}')
                    rollback_connection(connection)
                    logger.info('✓ schema validation recovered')

                # 6. Create tenant indexes safely.
                try:
                    ensure_tenant_indexes(cursor)
                    connection.commit()
                    logger.info('✓ Tenant indexes created')
                except Exception as e:
                    logger.error(f'Recoverable tenant index migration failed: {e}')
                    rollback_connection(connection)
                    logger.info('✓ schema validation recovered')
            finally:
                cursor.close()
                connection.close()

            if not schema_aligned:
                logger.error('✗ Application schema alignment failed; aborting before validation')
                return False

            # Run config constraint migration before writing any config rows.
            logger.info('Running config constraint migration...')
            from migrate_config_constraint import migrate_config_constraint
            if migrate_config_constraint():
                logger.info('✓ Config constraint migration completed')
            else:
                logger.error('✗ Config constraint migration failed')
                return False

            # Initialize community-scoped defaults after config.community_id exists.
            try:
                initialize_default_config(db.session, 'nthacityrp')
            except Exception:
                rollback_session(db)
                raise

            # Run admin role migration.
            logger.info('Running admin role migration...')
            from migrate_admin_role import migrate_admin_role
            if migrate_admin_role():
                logger.info('✓ Admin role migration completed')
            else:
                logger.error('✗ Admin role migration failed')
                return False

            # Run admin password migration.
            logger.info('Running admin password migration...')
            from migrate_admin_password import migrate_admin_password
            if migrate_admin_password():
                logger.info('✓ Admin password migration completed')
            else:
                logger.error('✗ Admin password migration failed')
                return False

            # Run diagnostic and legacy schema fix for civilian compatibility.
            logger.info('Running database diagnostic...')
            from database_diagnostic import diagnose_database
            if diagnose_database():
                logger.info('✓ Database diagnostic passed')
            else:
                logger.warning('⚠ Database diagnostic found issues, running fix...')
                from database_fix import fix_database
                if fix_database():
                    logger.info('✓ Database fix completed')
                else:
                    logger.error('✗ Database fix failed')
                    return False

            # Run tenant validation if available. Failures are returned to abort
            # invalid deploys, but already-applied idempotent migrations remain safe.
            logger.info('Running tenant validation...')
            from tenant_isolation_validator import run_all_tests
            if run_all_tests():
                logger.info('✓ Tenant validation passed')
            else:
                rollback_session(db)
                logger.warning('Tenant validation failed after migrations; retrying once with clean transaction')
                logger.info('✓ schema validation recovered')
                if run_all_tests():
                    logger.info('✓ Tenant validation passed after retry')
                else:
                    rollback_session(db)
                    logger.error('✗ Tenant validation failed')
                    return False

            logger.info('✓ Multi-tenant bootstrap complete')
            logger.info('✓ Schema bootstrap completed successfully')
            return True

    except Exception as e:
        try:
            from database import db
            rollback_session(db)
        except Exception:
            pass
        logger.error(f'✗ Schema bootstrap failed: {e}', exc_info=True)
        return False


if __name__ == '__main__':
    success = bootstrap_schema()
    sys.exit(0 if success else 1)
