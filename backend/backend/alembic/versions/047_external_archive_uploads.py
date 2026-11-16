"""Allow upload receipts owned by a project instead of a platform user."""
from alembic import op

revision = '047'
down_revision = '046'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('image_uploads', 'user_id', nullable=True)


def downgrade():
    op.alter_column('image_uploads', 'user_id', nullable=False)
