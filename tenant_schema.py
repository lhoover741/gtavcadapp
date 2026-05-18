"""Shared multi-tenant schema migration helpers.

These helpers are intentionally defensive because they run during Railway deploys
against existing production PostgreSQL databases. They never drop or recreate
production tables; they only add the nullable community_id column, backfill null
values, and create idempotent indexes.
"""

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

from platform_config import (
    DEFAULT_COMMUNITY_ID,
    DEFAULT_COMMUNITY_NAME,
    DEFAULT_COMMUNITY_CAD_NAME,
)

COMMUNITY_ID_DEFINITION = {
    'type': 'VARCHAR(64)',
    'nullable': True,
    'index': True,
}

# Required Phase 4 tenant-owned tables. Keep this list explicit so schema
# validators and deployment bootstrap recognize community_id everywhere.
REQUIRED_TENANT_TABLES = [
    'civilians',
    'arrests',
    'warrants',
    'bolos',
    'evidence',
    'traffic_stops',
    'citations',
    'jail_bookings',
    'inmates',
    'hearings',
    'dispatch_calls',
    'officer_sessions',
    'radio_logs',
    'alerts',
    'businesses',
    'licenses',
    'vehicles',
    'applications',
    'complaints',
    'audit_logs',
    'ai_generation_logs',
    'officer_notes',
    'case_files',
    'use_of_force_reports',
    'config',
    'server_status',
]

# Existing model table names not listed in the deployment request but already
# carrying community_id in models.py. radio_log is retained for backwards
# compatibility with the current SQLAlchemy model/table name.
ADDITIONAL_TENANT_TABLES = [
    'incidents',
    'calls_911',
    'activity_log',
    'known_associates',
    'radio_log',
]

TENANT_TABLES = REQUIRED_TENANT_TABLES + [
    table for table in ADDITIONAL_TENANT_TABLES if table not in REQUIRED_TENANT_TABLES
]

TENANT_SCHEMA_DEFINITIONS = {
    table: {
        'community_id': COMMUNITY_ID_DEFINITION.copy(),
    }
    for table in TENANT_TABLES
}


def _quote_identifier(identifier: str) -> str:
    """Quote a static PostgreSQL identifier from our allowlist."""
    if identifier not in TENANT_TABLES and not identifier.startswith('idx_'):
        raise ValueError(f'Unexpected identifier: {identifier}')
    return '"' + identifier.replace('"', '""') + '"'


def table_exists(cursor, table_name: str) -> bool:
    """Return True when a table exists in the current PostgreSQL schema."""
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


def ensure_tenant_community_columns(cursor, tables: Iterable[str] = None) -> None:
    """Add nullable community_id columns for tenant tables if missing."""
    for table_name in tables or TENANT_TABLES:
        if not table_exists(cursor, table_name):
            logger.info('✓ %s not present; skipping community_id migration', table_name)
            continue

        cursor.execute(
            f'ALTER TABLE {_quote_identifier(table_name)} '
            'ADD COLUMN IF NOT EXISTS community_id VARCHAR(64)'
        )
        logger.info('✓ community_id added to %s', table_name)


def backfill_default_community(cursor, tables: Iterable[str] = None) -> None:
    """Backfill community_id only where it is NULL using per-table savepoints."""
    for index, table_name in enumerate(tables or TENANT_TABLES):
        if not table_exists(cursor, table_name):
            logger.info('✓ %s not present; skipping community_id backfill', table_name)
            continue

        savepoint_name = f'community_backfill_{index}_{table_name}'
        cursor.execute(f'SAVEPOINT {savepoint_name}')

        try:
            if table_name == 'config':
                # Avoid duplicate (key, community_id) violations during repeat deploys.
                cursor.execute(
                    f'''
                    UPDATE {_quote_identifier(table_name)} c
                    SET community_id = %s
                    WHERE c.community_id IS NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {_quote_identifier(table_name)} existing
                          WHERE existing.key = c.key
                            AND existing.community_id = %s
                      )
                    ''',
                    (DEFAULT_COMMUNITY_ID, DEFAULT_COMMUNITY_ID),
                )
            else:
                cursor.execute(
                    f'UPDATE {_quote_identifier(table_name)} '
                    'SET community_id = %s WHERE community_id IS NULL',
                    (DEFAULT_COMMUNITY_ID,),
                )

            rows_updated = cursor.rowcount
            cursor.execute(
                f'SELECT COUNT(*) FROM {_quote_identifier(table_name)} WHERE community_id IS NULL'
            )
            remaining_null_rows = int(cursor.fetchone()[0])
            cursor.execute(f'RELEASE SAVEPOINT {savepoint_name}')
            logger.info(
                '✓ community_id backfill complete for %s (%s rows updated, %s NULL remaining)',
                table_name,
                rows_updated,
                remaining_null_rows,
            )
        except Exception as error:
            cursor.execute(f'ROLLBACK TO SAVEPOINT {savepoint_name}')
            cursor.execute(f'RELEASE SAVEPOINT {savepoint_name}')
            logger.exception(
                '✗ community_id backfill failed for %s; rolled back this table only: %s',
                table_name,
                error,
            )


def ensure_tenant_indexes(cursor, tables: Iterable[str] = None) -> None:
    """Create community_id indexes for tenant tables if missing."""
    for table_name in tables or TENANT_TABLES:
        if not table_exists(cursor, table_name):
            logger.info('✓ %s not present; skipping community_id index', table_name)
            continue

        index_name = f'idx_{table_name}_community_id'
        cursor.execute(
            f'CREATE INDEX IF NOT EXISTS {_quote_identifier(index_name)} '
            f'ON {_quote_identifier(table_name)}(community_id)'
        )
        logger.info('✓ community_id index verified for %s', table_name)
