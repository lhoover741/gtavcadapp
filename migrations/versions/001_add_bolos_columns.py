"""Add missing columns to bolos table.

Revision ID: 001
Revises: 
Create Date: 2026-05-07 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Add missing columns to bolos table."""
    # Add columns with IF NOT EXISTS equivalent using try/except
    with op.batch_alter_table('bolos', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('suspect_name', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('last_seen_location', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('threat_level', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('issued_by', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('auto_generated', sa.Boolean(), server_default=sa.false(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        except Exception:
            pass


def downgrade():
    """Remove added columns from bolos table."""
    with op.batch_alter_table('bolos', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('auto_generated')
        batch_op.drop_column('issued_by')
        batch_op.drop_column('threat_level')
        batch_op.drop_column('last_seen_location')
        batch_op.drop_column('description')
        batch_op.drop_column('suspect_name')
