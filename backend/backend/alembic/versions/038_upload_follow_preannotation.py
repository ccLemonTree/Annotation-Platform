"""Allow one preannotation task per project to receive newly uploaded images."""
from alembic import op
import sqlalchemy as sa

revision = '038'
down_revision = '037'
branch_labels = None
depends_on = None

TABLES = [('automation_jobs', 'uq_automation_follow_uploads'), ('project_work_jobs', 'uq_work_follow_uploads')]


def upgrade():
    for table, index in TABLES:
        op.add_column(table, sa.Column('follow_uploads', sa.Boolean(), nullable=False, server_default=sa.false()))
        op.create_index(index, table, ['project_id'], unique=True, postgresql_where=sa.text('follow_uploads'))


def downgrade():
    for table, index in TABLES:
        op.drop_index(index, table_name=table)
        op.drop_column(table, 'follow_uploads')
