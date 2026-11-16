"""Explicit repository groups leave names, identities and artifact files intact."""
from alembic import op
import sqlalchemy as sa

revision='031'
down_revision='030'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('model_repository_items',sa.Column('group_name',sa.String(100),nullable=False,server_default=''))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM model_repository_items WHERE group_name<>''")):
        raise RuntimeError('已有显式模型组，请保留字段并回退代码，不能丢失分组信息')
    op.drop_column('model_repository_items','group_name')
