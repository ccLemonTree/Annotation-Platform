"""Add Docker training runs and exclusive GPU holds, retaining all existing tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='018'
down_revision='017'
branch_labels=None
depends_on=None


def upgrade():
    inspector=sa.inspect(op.get_bind())
    if any(inspector.has_table(name) for name in ('training_runs','training_gpu_holds')):
        raise RuntimeError('018检测到已有训练表，禁止覆盖')
    op.create_table('training_runs',sa.Column('id',postgresql.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('user_id',sa.Integer(),nullable=False),sa.Column('name',sa.String(200),nullable=False),
        sa.Column('kind',sa.String(30),nullable=False),sa.Column('status',sa.String(30),nullable=False),
        sa.Column('config',postgresql.JSONB(),nullable=False),sa.Column('source',postgresql.JSONB(),nullable=False),
        sa.Column('server',postgresql.JSONB(),nullable=False),sa.Column('output_dir',sa.String(500),nullable=False),
        sa.Column('inspection',postgresql.JSONB(),nullable=False),sa.Column('error',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(),server_default=sa.func.now(),nullable=False))
    op.create_index('idx_training_run_tenant_created','training_runs',['tenant_id','created_at'])
    op.create_table('training_gpu_holds',sa.Column('gpu_uuid',sa.String(100),primary_key=True),
        sa.Column('run_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('training_runs.id',ondelete='RESTRICT'),nullable=False))
    op.create_index('ix_training_gpu_holds_run_id','training_gpu_holds',['run_id'])


def downgrade():
    raise RuntimeError('训练记录及资源hold不得自动删除')
