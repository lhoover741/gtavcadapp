"""Add advanced civilian profile fields.

Revision ID: 004
Revises: 003
Create Date: 2026-05-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade():
    """Add advanced civilian profile columns to civilians table."""
    with op.batch_alter_table('civilians', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('addiction_status', sa.String(255), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('addiction_severity', sa.String(64), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('weapon_permit_type', sa.String(255), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('driving_history', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('insurance_status', sa.String(64), server_default='Valid', nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('emergency_contact_name', sa.String(255), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('emergency_contact_phone', sa.String(64), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('emergency_contact_relationship', sa.String(64), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('medical_conditions', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('medications', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('allergies', sa.Text(), nullable=True))
        except Exception:
            pass


def downgrade():
    """Remove advanced civilian profile columns from civilians table."""
    with op.batch_alter_table('civilians', schema=None) as batch_op:
        batch_op.drop_column('allergies')
        batch_op.drop_column('medications')
        batch_op.drop_column('medical_conditions')
        batch_op.drop_column('emergency_contact_relationship')
        batch_op.drop_column('emergency_contact_phone')
        batch_op.drop_column('emergency_contact_name')
        batch_op.drop_column('insurance_status')
        batch_op.drop_column('driving_history')
        batch_op.drop_column('weapon_permit_type')
        batch_op.drop_column('addiction_severity')
        batch_op.drop_column('addiction_status')
