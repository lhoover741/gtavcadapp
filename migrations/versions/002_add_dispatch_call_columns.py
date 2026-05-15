"""Add assigned_unit and updated_at columns to dispatch_calls table.

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
    """Add missing columns to dispatch_calls table."""
    with op.batch_alter_table('dispatch_calls', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('assigned_unit', sa.Text(), nullable=True))
        except Exception:
            pass

        try:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        except Exception:
            pass


def downgrade():
    """Remove added columns from dispatch_calls table."""
    with op.batch_alter_table('dispatch_calls', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('assigned_unit')
