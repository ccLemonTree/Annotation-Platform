"""Add optional encrypted jump connections without changing existing servers."""
from alembic import op
import sqlalchemy as sa

revision='028'
down_revision='027'
branch_labels=None
depends_on=None


def upgrade():
    for name,type_ in [('jump_host',sa.String(45)),('jump_port',sa.Integer()),
                       ('jump_username',sa.String(64)),('jump_password_encrypted',sa.Text())]:
        op.add_column('gpu_servers',sa.Column(name,type_,nullable=True))


def downgrade():
    raise RuntimeError('跳板机凭据不得自动删除，请保留数据并回退代码')
