"""Add editable chat prompts without changing existing model settings or revisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '019'
down_revision = '018'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('model_services', sa.Column('chat_prompts', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    raise RuntimeError('已保存的提示词不得自动删除；回退代码时保留此列')
