"""Store the managed workflow orchestration model chosen by each tenant."""
from alembic import op
import sqlalchemy as sa

revision = '058'
down_revision = '057'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('workflow_model_service_id', sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column('tenants', 'workflow_model_service_id')
