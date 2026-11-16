"""Keep model connection checks separate from enabled configuration."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '033'
down_revision = '032'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('model_services', sa.Column('connection_check', postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column('model_services', 'connection_check')
