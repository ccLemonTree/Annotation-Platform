"""Store separate review states for mixed annotations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '048'
down_revision = '047'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('annotations', sa.Column('review_by_kind', JSONB(), nullable=True))


def downgrade():
    op.drop_column('annotations', 'review_by_kind')
