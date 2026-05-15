"""Add civilian_id to warrants and arrests tables.

Revision ID: 003
Revises: 002
Create Date: 2026-05-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    """Add civilian_id column to warrants and arrests tables."""
    with op.batch_alter_table('warrants', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('civilian_id', sa.String(64), nullable=True))
        except Exception:
            pass

    with op.batch_alter_table('arrests', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('civilian_id', sa.String(64), nullable=True))
        except Exception:
            pass


def downgrade():
    """Remove civilian_id column from warrants and arrests tables."""
    with op.batch_alter_table('warrants', schema=None) as batch_op:
        batch_op.drop_column('civilian_id')

    with op.batch_alter_table('arrests', schema=None) as batch_op:
        batch_op.drop_column('civilian_id')
