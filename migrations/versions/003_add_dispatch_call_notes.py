"""Add notes column to dispatch_calls table.

Revision ID: 003_dispatch_notes
Revises: 002
Create Date: 2026-05-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003_dispatch_notes'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    """Add notes column to dispatch_calls table."""
    with op.batch_alter_table('dispatch_calls', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))
        except Exception:
            pass


def downgrade():
    """Remove notes column from dispatch_calls table."""
    with op.batch_alter_table('dispatch_calls', schema=None) as batch_op:
        batch_op.drop_column('notes')
