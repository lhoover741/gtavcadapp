#!/usr/bin/env python
"""Safely sync PostgreSQL schema with SQLAlchemy model."""

import sys
import logging


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def quote_identifier(identifier):
    """Safely quote an application-controlled SQL identifier."""
    if not identifier or not identifier.replace('_', '').isalnum():
        raise ValueError(f'Unexpected SQL identifier: {identifier}')
    return '"' + identifier.replace('"', '""') + '"'


def table_exists(cursor, table_name):
    """Return True if a table exists in the current PostgreSQL schema."""
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = %s
        )
        """,
        (table_name,),
    )
    return bool(cursor.fetchone()[0])


def column_exists(cursor, table_name, column_name):
    """Return True if a column exists in the current PostgreSQL schema."""
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def column_sql_definition(column):
    """Convert a SQLAlchemy column into a safe additive PostgreSQL definition."""
    column_type = column.type

    type_name = column_type.__class__.__name__.lower()

    if 'text' in type_name:
        sql_type = 'TEXT'
    elif 'string' in type_name or 'varchar' in type_name:
        length = getattr(column_type, 'length', None) or 255
        sql_type = f'VARCHAR({length})'
    elif 'integer' in type_name:
        sql_type = 'INTEGER'
    elif 'datetime' in type_name:
        sql_type = 'TIMESTAMP'
    elif type_name == 'date':
        sql_type = 'DATE'
    elif type_name == 'time':
        sql_type = 'TIME'
    elif 'boolean' in type_name:
        sql_type = 'BOOLEAN'
    elif 'float' in type_name:
        sql_type = 'DOUBLE PRECISION'
    elif 'numeric' in type_name or 'decimal' in type_name:
        sql_type = 'NUMERIC'
    else:
        try:
            compiled = str(column_type).upper()
            sql_type = compiled if compiled else 'TEXT'
        except Exception:
            sql_type = 'TEXT'

    if column.primary_key:
        # db.create_all owns table creation and primary-key constraints. Schema
        # drift repair only adds nullable data columns to existing tables.
        return None

    return sql_type


def rollback_session(db):
    """Rollback the Flask-SQLAlchemy session if available."""
    try:
        db.session.rollback()
        logger.info('✓ transaction rollback handled')
    except Exception as rollback_error:
        logger.warning('Unable to rollback SQLAlchemy session cleanly: %s', rollback_error)


def rollback_connection(connection):
    """Rollback a raw DB-API connection after a recoverable migration error."""
    try:
        connection.rollback()
        logger.info('✓ transaction rollback handled')
    except Exception as rollback_error:
        logger.warning('Unable to rollback raw database connection cleanly: %s', rollback_error)


def ensure_model_columns(cursor, connection, model_class):
    """Add missing nullable columns for a SQLAlchemy model without data loss."""
    table_name = model_class.__tablename__

    if not table_exists(cursor, table_name):
        logger.info('✓ %s not present; db.create_all will create it when needed', table_name)
        return True

    aligned = True
    for column in model_class.__table__.columns:
        column_name = column.name
        if column_exists(cursor, table_name, column_name):
            if table_name == 'dispatch_calls' and column_name == 'assigned_unit':
                logger.info('✓ dispatch_calls.assigned_unit verified')
            continue

        sql_type = column_sql_definition(column)
        if not sql_type:
            logger.warning('Skipping missing primary-key column %s.%s; manual migration required', table_name, column_name)
            continue

        try:
            cursor.execute(
                f'ALTER TABLE {quote_identifier(table_name)} '
                f'ADD COLUMN IF NOT EXISTS {quote_identifier(column_name)} {sql_type}'
            )
            connection.commit()
            logger.info('✓ Added %s.%s as %s', table_name, column_name, sql_type)
        except Exception as exc:
            logger.error('Recoverable schema migration failed for %s.%s: %s', table_name, column_name, exc)
            rollback_connection(connection)
            aligned = False

    if table_name == 'dispatch_calls':
        required_dispatch_columns = {
            'call_id', 'caller_name', 'location', 'description', 'priority',
            'status', 'assigned_unit', 'created_at', 'updated_at', 'community_id',
        }
        missing_required = [
            col for col in sorted(required_dispatch_columns)
            if not column_exists(cursor, table_name, col)
        ]
        if missing_required:
            logger.error('dispatch_calls schema still missing required columns: %s', missing_required)
            aligned = False
        else:
            logger.info('✓ dispatch_calls schema aligned')

    return aligned


def ensure_application_schema(cursor, connection):
    """Synchronize additive schema drift for all deployed SQLAlchemy models."""
    from database import db
    import models  # noqa: F401 - imports model classes into SQLAlchemy metadata

    model_classes = sorted(
        (mapper.class_ for mapper in db.Model.registry.mappers),
        key=lambda cls: getattr(cls, '__tablename__', cls.__name__),
    )

    results = []
    for model_class in model_classes:
        if getattr(model_class, '__tablename__', None):
            results.append(ensure_model_columns(cursor, connection, model_class))

    return all(results)


def sync_schema():
    """Sync PostgreSQL schema with SQLAlchemy model."""
    connection = None
    cursor = None
    try:
        logger.info('Starting schema sync...')

        from server import app
        from database import db
        from tenant_schema import (
            ensure_tenant_community_columns,
            ensure_tenant_indexes,
        )

        with app.app_context():
            connection = db.engine.raw_connection()
            cursor = connection.cursor()

            logger.info('Checking SQLAlchemy model columns for additive schema drift...')
            schema_ok = ensure_application_schema(cursor, connection)

            try:
                logger.info('Checking tenant community_id columns...')
                ensure_tenant_community_columns(cursor)
                ensure_tenant_indexes(cursor)
                connection.commit()
                logger.info('✓ Tenant indexes created')
            except Exception as exc:
                logger.error('Recoverable tenant schema sync failed: %s', exc)
                rollback_connection(connection)
                schema_ok = False

            if not schema_ok:
                logger.warning('✓ schema validation recovered')
                logger.error('✗ Schema sync completed with unresolved schema drift')
                return False

            logger.info('✓ schema validation recovered')
            logger.info('✓ Schema sync completed successfully')
            return True

    except Exception as e:
        if connection:
            rollback_connection(connection)
        try:
            from database import db
            rollback_session(db)
        except Exception:
            pass
        logger.error(f'✗ Schema sync failed: {e}', exc_info=True)
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == '__main__':
    success = sync_schema()
    sys.exit(0 if success else 1)
