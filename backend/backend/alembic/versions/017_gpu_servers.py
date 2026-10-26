"""Add encrypted GPU connection records without touching existing business data."""
from alembic import op
import sqlalchemy as sa

revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table('gpu_servers'):
        raise RuntimeError('017检测到已有gpu_servers表，禁止覆盖，请先核对版本和结构')
    op.execute(sa.text('''CREATE TABLE gpu_servers (
        id UUID PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id),
        name VARCHAR(200) NOT NULL, host VARCHAR(45) NOT NULL,
        port INTEGER DEFAULT 22 NOT NULL, username VARCHAR(64) DEFAULT 'root' NOT NULL,
        password_encrypted TEXT, enabled BOOLEAN DEFAULT true NOT NULL,
        revision INTEGER DEFAULT 1 NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)'''))


def downgrade():
    raise RuntimeError('017包含加密服务器凭据，禁止自动删除业务数据')
