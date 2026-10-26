"""Independent model distribution records; no change to training or serving data."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision='024'
down_revision='023'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('model_deployments',
        sa.Column('id',pg.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('user_id',sa.Integer(),nullable=False),
        sa.Column('run_id',pg.UUID(as_uuid=True),sa.ForeignKey('training_runs.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('target_key',sa.String(64),nullable=False),
        sa.Column('source',pg.JSONB(),nullable=False),sa.Column('target',pg.JSONB(),nullable=False),
        sa.Column('inspection',pg.JSONB(),nullable=False),sa.Column('logs',pg.JSONB(),nullable=False),
        sa.Column('error',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(),server_default=sa.func.now(),nullable=False))
    op.create_index('idx_model_deployment_tenant_run','model_deployments',['tenant_id','run_id'])
    op.create_index('uq_model_deployment_busy_target','model_deployments',['target_key'],unique=True,
        postgresql_where=sa.text("status IN ('transferring','activating','rolling_back','unknown')"))


def downgrade():
    raise RuntimeError('模型下发日志与恢复记录不得自动删除')
