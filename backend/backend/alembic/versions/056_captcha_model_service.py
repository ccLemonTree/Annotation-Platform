"""Store the managed captcha model chosen by each tenant."""
from alembic import op
import sqlalchemy as sa

revision = '056'
down_revision = '055'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('captcha_model_service_id', sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column('tenants', 'captcha_model_service_id')
