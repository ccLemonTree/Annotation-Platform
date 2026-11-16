"""Record the project that received a resumable upload."""
from alembic import op
import sqlalchemy as sa

revision = '037'
down_revision = '036'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('image_uploads', sa.Column('project_id', sa.Uuid(),
        sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_upload_project_history', 'image_uploads', ['project_id', 'updated_at'])


def downgrade():
    op.drop_index('ix_upload_project_history', table_name='image_uploads')
    op.drop_column('image_uploads', 'project_id')
