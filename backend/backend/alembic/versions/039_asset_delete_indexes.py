"""Index foreign keys used by asset deletion."""
from alembic import op

revision = '039'
down_revision = '038'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_project_work_items_asset_id', 'project_work_items', ['asset_id'])
    op.create_index('ix_work_activity_intervals_asset_id', 'work_activity_intervals', ['asset_id'])


def downgrade():
    op.drop_index('ix_work_activity_intervals_asset_id', table_name='work_activity_intervals')
    op.drop_index('ix_project_work_items_asset_id', table_name='project_work_items')
