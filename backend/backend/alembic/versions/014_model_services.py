"""Configured model inference services, independent of training artifacts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade():
    metadata = sa.MetaData()
    sa.Table('tenants', metadata, sa.Column('id', sa.BigInteger, primary_key=True))
    table = sa.Table('model_services', metadata,
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.BigInteger, sa.ForeignKey('tenants.id'), nullable=False),
        *[sa.Column(name, sa.String(length), nullable=False) for name, length in (
            ('key', 80), ('name', 200), ('kind', 30), ('protocol', 30), ('base_url', 500),
            ('endpoint', 200), ('model_name', 200), ('api_key_env', 100))],
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('timeout_seconds', sa.Integer, nullable=False),
        sa.Column('confidence', sa.Float, nullable=False),
        sa.Column('iou', sa.Float, nullable=False),
        sa.Column('labels', postgresql.JSONB, nullable=False),
        sa.Column('label_mapping', postgresql.JSONB, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.UniqueConstraint('tenant_id', 'key', name='uq_model_service_tenant_key'))
    table.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('模型配置包含业务数据；请备份并显式处理，禁止自动删除')
