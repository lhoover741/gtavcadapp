"""Add advanced character engine fields to civilians table.

Revision ID: 002
Revises: 001
Create Date: 2026-05-07 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    """Add advanced character engine columns to civilians table."""
    with op.batch_alter_table('civilians', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('nickname', sa.String(255), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('aliases', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('employment_history', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('gang_rank', sa.String(64), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('habits', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('social_behavior', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('weapon_access', sa.String(64), server_default='None', nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('violence_history', sa.String(64), server_default='None', nullable=True))
        except Exception:
            pass


def downgrade():
    """Remove advanced character engine columns from civilians table."""
    with op.batch_alter_table('civilians', schema=None) as batch_op:
        batch_op.drop_column('violence_history')
        batch_op.drop_column('weapon_access')
        batch_op.drop_column('social_behavior')
        batch_op.drop_column('habits')
        batch_op.drop_column('gang_rank')
        batch_op.drop_column('employment_history')
        batch_op.drop_column('aliases')
        batch_op.drop_column('nickname')
