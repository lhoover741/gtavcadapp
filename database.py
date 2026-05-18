import os
import logging
from flask_sqlalchemy import SQLAlchemy

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL', '')

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)


def is_postgresql_database_url(database_url):
    """Return True when the configured SQLAlchemy URL uses PostgreSQL."""
    return database_url.startswith(('postgresql://', 'postgresql+'))


db = SQLAlchemy()


def verify_schema(app):
    """Verify and fix schema on startup."""
    try:
        with app.app_context():
            from models import Civilian

            # Get all columns from SQLAlchemy model
            expected_columns = {col.name for col in Civilian.__table__.columns}

            # Get all columns from database
            inspector = db.inspect(db.engine)
            try:
                actual_columns = {col['name'] for col in inspector.get_columns('civilians')}
            except Exception:
                # Table doesn't exist yet — create_all will handle it
                logger.info('civilians table not found; will be created by create_all')
                return

            missing = expected_columns - actual_columns
            extra = actual_columns - expected_columns

            if missing:
                logger.warning(f'Missing columns in PostgreSQL: {missing}')
                logger.info('Running schema sync to add missing columns...')
                try:
                    from schema_sync import sync_schema
                    sync_schema()
                except Exception as e:
                    logger.error(f'Schema sync failed: {e}')
            else:
                logger.info('✓ Schema is in sync')

            if extra:
                logger.info(f'Extra columns in PostgreSQL (safe to ignore): {extra}')

    except Exception as e:
        logger.warning(f'Schema verification failed: {e}')


def configure_database(app):
    """Configure database and create tables if needed."""
    if DATABASE_URL:
        app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        engine_options = {
            'pool_pre_ping': True,
            'pool_recycle': int(os.environ.get('SQLALCHEMY_POOL_RECYCLE', '280')),
        }
        if is_postgresql_database_url(DATABASE_URL):
            engine_options.update({
                'pool_size': int(os.environ.get('SQLALCHEMY_POOL_SIZE', '5')),
                'max_overflow': int(os.environ.get('SQLALCHEMY_MAX_OVERFLOW', '10')),
            })
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options
        db.init_app(app)

        with app.app_context():
            try:
                # Create all tables from models
                db.create_all()
                logger.info('✓ Database tables created/verified')

                # Verify schema and add any missing columns
                verify_schema(app)

            except Exception as e:
                logger.error(f'Failed to create tables: {e}')

        return True
    return False
