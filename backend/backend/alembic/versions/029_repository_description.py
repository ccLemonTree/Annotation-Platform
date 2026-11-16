"""Allow model version notes while preserving immutable artifact metadata."""
from alembic import op
import sqlalchemy as sa

revision='029'
down_revision='028'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('model_repository_items',sa.Column('description',sa.Text(),nullable=False,server_default=''))


def downgrade():
    raise RuntimeError('模型备注不得自动删除，请保留字段并回退代码')
