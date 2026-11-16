"""Reference immutable repository models without duplicating legacy releases."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision='027'
down_revision='026'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('model_deployments',sa.Column('repository_id',pg.UUID(as_uuid=True),
        sa.ForeignKey('model_repository_items.id',ondelete='RESTRICT'),nullable=True))


def downgrade():
    raise RuntimeError('仓库模型下发引用和恢复历史不得自动删除')
