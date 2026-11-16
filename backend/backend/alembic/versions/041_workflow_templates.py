"""Project workflow templates."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '041'
down_revision = '040'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('project_workflow_templates',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('config', JSONB(), nullable=False),
        sa.UniqueConstraint('project_id','name',name='uq_project_workflow_template_name'))


def downgrade():
    op.drop_table('project_workflow_templates')
