"""Convert config uniqueness to (key, community_id).

Revision ID: 009
Revises: 008
Create Date: 2026-05-10 13:30:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    """Drop legacy key-only uniqueness and add tenant-aware uniqueness."""
    op.execute('ALTER TABLE config DROP CONSTRAINT IF EXISTS config_key_key;')
    op.execute('ALTER TABLE config DROP CONSTRAINT IF EXISTS unique_config_per_community;')
    op.execute('ALTER TABLE config DROP CONSTRAINT IF EXISTS uq_config_key_community;')
    op.execute(
        'ALTER TABLE config ADD CONSTRAINT uq_config_key_community UNIQUE (key, community_id);'
    )


def downgrade():
    """Revert to legacy single-column key uniqueness."""
    op.execute('ALTER TABLE config DROP CONSTRAINT IF EXISTS uq_config_key_community;')
    op.execute('ALTER TABLE config DROP CONSTRAINT IF EXISTS unique_config_per_community;')
    op.execute('ALTER TABLE config ADD CONSTRAINT config_key_key UNIQUE (key);')
