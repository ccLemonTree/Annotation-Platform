"""Persist background freeze requests."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '052'
down_revision = '051'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('export_snapshot_jobs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('request', JSONB(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('packed', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(1000)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_export_snapshot_jobs_project_id', 'export_snapshot_jobs', ['project_id'])
    op.create_index('ix_export_snapshot_jobs_status', 'export_snapshot_jobs', ['status'])


def downgrade():
    op.drop_table('export_snapshot_jobs')
