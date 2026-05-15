"""Add missing civilian columns to match SQLAlchemy model.

Revision ID: 005
Revises: 004
Create Date: 2026-05-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    """Add missing columns to civilians table."""
    with op.batch_alter_table('civilians', schema=None) as batch_op:
        # Core identification fields
        try:
            batch_op.add_column(sa.Column('full_name', sa.String(255), nullable=True))
        except Exception:
            pass

        # Date/time fields
        try:
            batch_op.add_column(sa.Column('dob', sa.String(64), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('phone', sa.String(64), nullable=True))
        except Exception:
            pass

        # Location fields
        try:
            batch_op.add_column(sa.Column('last_known_location', sa.String(255), nullable=True))
        except Exception:
            pass

        # Background/history fields
        try:
            batch_op.add_column(sa.Column('biography', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('mental_state_notes', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))
        except Exception:
            pass

        # Character fields
        try:
            batch_op.add_column(sa.Column('nickname', sa.String(255), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('aliases', sa.Text(), nullable=True))
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

        # Employment
        try:
            batch_op.add_column(sa.Column('employment_history', sa.Text(), nullable=True))
        except Exception:
            pass

        # Gang/criminal fields
        try:
            batch_op.add_column(sa.Column('gang_rank', sa.String(64), nullable=True))
        except Exception:
            pass

        # Weapon/violence fields
        try:
            batch_op.add_column(sa.Column('weapon_access', sa.String(64), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('violence_history', sa.String(64), nullable=True))
        except Exception:
            pass

        # AI generation flag
        try:
            batch_op.add_column(sa.Column('ai_generated', sa.Boolean(), server_default='false', nullable=True))
        except Exception:
            pass

        # Advanced civilian profile fields (from Phase 4)
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
    """Remove added columns from civilians table."""
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
        batch_op.drop_column('ai_generated')
        batch_op.drop_column('violence_history')
        batch_op.drop_column('weapon_access')
        batch_op.drop_column('gang_rank')
        batch_op.drop_column('employment_history')
        batch_op.drop_column('social_behavior')
        batch_op.drop_column('habits')
        batch_op.drop_column('aliases')
        batch_op.drop_column('nickname')
        batch_op.drop_column('notes')
        batch_op.drop_column('mental_state_notes')
        batch_op.drop_column('biography')
        batch_op.drop_column('last_known_location')
        batch_op.drop_column('phone')
        batch_op.drop_column('dob')
        batch_op.drop_column('full_name')
