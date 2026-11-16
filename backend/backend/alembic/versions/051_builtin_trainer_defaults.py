"""Store tenant defaults for built-in training recipes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '051'
down_revision = '050'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('training_defaults', JSONB(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column('tenants', 'training_defaults')
