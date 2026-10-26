"""Frozen additive schema for immutable validation truth and model reports."""
from alembic import op
import sqlalchemy as sa

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None

TABLES = ('validation_sets', 'validation_runs', 'validation_items')
DDL = [
    '''CREATE TABLE validation_sets (
        id UUID PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id),
        project_id UUID REFERENCES projects(id) ON DELETE SET NULL, user_id INTEGER NOT NULL,
        name VARCHAR(200) NOT NULL, labels JSONB NOT NULL, samples JSONB NOT NULL,
        source JSONB NOT NULL, total INTEGER NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''',
    'CREATE INDEX idx_validation_set_tenant_created ON validation_sets(tenant_id,created_at)',
    '''CREATE TABLE validation_runs (
        id UUID PRIMARY KEY, set_id UUID NOT NULL REFERENCES validation_sets(id) ON DELETE CASCADE,
        tenant_id BIGINT NOT NULL REFERENCES tenants(id), user_id INTEGER NOT NULL,
        name VARCHAR(200) NOT NULL, status VARCHAR(30) NOT NULL, model_snapshot JSONB NOT NULL,
        config JSONB NOT NULL, metrics JSONB NOT NULL, total INTEGER NOT NULL,
        processed INTEGER NOT NULL, failed_count INTEGER NOT NULL, error VARCHAR(500),
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''',
    'CREATE INDEX idx_validation_run_tenant_created ON validation_runs(tenant_id,created_at)',
    '''CREATE TABLE validation_items (
        id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES validation_runs(id) ON DELETE CASCADE,
        asset_id UUID NOT NULL, original_name VARCHAR(255) NOT NULL, width INTEGER NOT NULL,
        height INTEGER NOT NULL, status VARCHAR(30) NOT NULL, attempt INTEGER NOT NULL,
        truth JSONB NOT NULL, predictions JSONB NOT NULL, evaluation JSONB NOT NULL,
        error VARCHAR(500), created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''',
    'CREATE INDEX idx_validation_item_claim ON validation_items(status,created_at)',
    'CREATE INDEX idx_validation_item_run ON validation_items(run_id,status)',
]


def upgrade():
    bind = op.get_bind()
    existing = [table for table in TABLES if sa.inspect(bind).has_table(table)]
    if existing:
        raise RuntimeError('016检测到已有同名表，禁止覆盖；请先核对结构与版本：' + ','.join(existing))
    for statement in DDL:
        op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError('016包含验证真值及模型报告，禁止自动删除业务数据')
