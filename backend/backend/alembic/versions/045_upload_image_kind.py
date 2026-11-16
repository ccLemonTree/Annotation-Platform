"""Remember the project image kind on resumable uploads."""
from alembic import op
import sqlalchemy as sa

revision = '045'
down_revision = '044'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('image_uploads', sa.Column('image_kind', sa.String(12), nullable=False, server_default='originals'))


def downgrade():
    op.drop_column('image_uploads', 'image_kind')
