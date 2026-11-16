"""Register container trainers per tenant."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '050'
down_revision = '049'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'training_trainers',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.BigInteger(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('definition', JSONB(), nullable=False),
    )
    op.create_index('ix_training_trainers_tenant_id', 'training_trainers', ['tenant_id'])


def downgrade():
    op.drop_table('training_trainers')
