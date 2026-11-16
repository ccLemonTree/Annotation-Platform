"""Allow administrators to designate Docker training servers."""
from alembic import op
import sqlalchemy as sa

revision = '040'
down_revision = '039'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('gpu_servers', sa.Column('training_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('gpu_servers', 'training_enabled')
