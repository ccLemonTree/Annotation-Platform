"""Local model versions, saved service targets, and batch history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision='025'
down_revision='024'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('model_releases',
        sa.Column('id',pg.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('user_id',sa.Integer(),nullable=False),
        sa.Column('run_id',pg.UUID(as_uuid=True),sa.ForeignKey('training_runs.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('name',sa.String(100),nullable=False),sa.Column('version',sa.String(100),nullable=False),
        sa.Column('runtime',sa.String(30),nullable=False),sa.Column('status',sa.String(30),nullable=False),
        sa.Column('source',pg.JSONB(),nullable=False),sa.Column('archive',pg.JSONB(),nullable=False),
        sa.Column('logs',pg.JSONB(),nullable=False),sa.Column('error',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('tenant_id','name','version',name='uq_model_release_version'))
    op.create_table('deployment_targets',
        sa.Column('id',pg.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('name',sa.String(100),nullable=False),sa.Column('kind',sa.String(10),nullable=False),
        sa.Column('target_key',sa.String(64),nullable=False),sa.Column('target',pg.JSONB(),nullable=False),
        sa.Column('observation',pg.JSONB(),nullable=False),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('tenant_id','kind','target_key',name='uq_deployment_target'))
    op.create_table('deployment_leases',sa.Column('resource',sa.String(64),primary_key=True),sa.Column('job_id',pg.UUID(as_uuid=True),nullable=False))
    op.create_index('ix_deployment_leases_job_id','deployment_leases',['job_id'])
    op.alter_column('model_deployments','run_id',nullable=True)
    op.add_column('model_deployments',sa.Column('release_id',pg.UUID(as_uuid=True),sa.ForeignKey('model_releases.id',ondelete='RESTRICT'),nullable=True))
    op.add_column('model_deployments',sa.Column('target_id',pg.UUID(as_uuid=True),sa.ForeignKey('deployment_targets.id',ondelete='RESTRICT'),nullable=True))
    op.add_column('model_deployments',sa.Column('batch_id',pg.UUID(as_uuid=True),nullable=True))
    op.add_column('model_deployments',sa.Column('kind',sa.String(10),nullable=False,server_default='model'))
    op.create_index('ix_model_deployments_batch_id','model_deployments',['batch_id'])


def downgrade():
    raise RuntimeError('模型归档、服务器版本台账和恢复历史不得自动删除')
