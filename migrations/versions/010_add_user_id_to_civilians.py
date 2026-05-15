"""Add nullable user ownership to civilian profiles.

Revision ID: 010_add_user_id_to_civilians
Revises: 009_config_unique_key_community
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    try:
        columns = {column['name'] for column in inspector.get_columns('civilians')}
    except Exception:
        columns = set()

    if 'user_id' not in columns:
        with op.batch_alter_table('civilians', schema=None) as batch_op:
            batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))

    try:
        indexes = {index.get('name') for index in inspector.get_indexes('civilians')}
    except Exception:
        indexes = set()
    if 'idx_civilians_user_id' not in indexes:
        op.create_index('idx_civilians_user_id', 'civilians', ['user_id'], unique=False)


def downgrade():
    try:
        op.drop_index('idx_civilians_user_id', table_name='civilians')
    except Exception:
        pass
    with op.batch_alter_table('civilians', schema=None) as batch_op:
        try:
            batch_op.drop_column('user_id')
        except Exception:
            pass
