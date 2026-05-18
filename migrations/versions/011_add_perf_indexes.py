"""add performance indexes for shared CAD queries

Revision ID: 011_add_perf_indexes
Revises: 010_add_user_id_to_civilians
Create Date: 2026-05-18
"""
from alembic import op

revision = '011_add_perf_indexes'
down_revision = '010_add_user_id_to_civilians'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_community_id ON users (community_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dispatch_calls_community_created_at ON dispatch_calls (community_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_user_created_at ON notifications (target_user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_plate ON vehicles (plate)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_civilians_user_id ON civilians (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_warrants_community_created_at ON warrants (community_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bolos_community_created_at ON bolos (community_id, created_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_bolos_community_created_at")
    op.execute("DROP INDEX IF EXISTS ix_warrants_community_created_at")
    op.execute("DROP INDEX IF EXISTS ix_civilians_user_id")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_plate")
    op.execute("DROP INDEX IF EXISTS ix_notifications_created_at")
    op.execute("DROP INDEX IF EXISTS ix_notifications_user_created_at")
    op.execute("DROP INDEX IF EXISTS ix_dispatch_calls_community_created_at")
    op.execute("DROP INDEX IF EXISTS ix_users_community_id")
    op.execute("DROP INDEX IF EXISTS ix_users_username")
    op.execute("DROP INDEX IF EXISTS ix_users_email")
