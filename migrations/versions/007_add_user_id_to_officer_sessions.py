"""Add user_id to officer_sessions table.

Revision ID: 007
Revises: 006
Create Date: 2026-05-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade():
    """Add user_id column to officer_sessions table."""
    with op.batch_alter_table('officer_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_officer_sessions_user_id', 'users', ['user_id'], ['id'])


def downgrade():
    """Remove user_id column from officer_sessions table."""
    with op.batch_alter_table('officer_sessions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_officer_sessions_user_id', type_='foreignkey')
        batch_op.drop_column('user_id')