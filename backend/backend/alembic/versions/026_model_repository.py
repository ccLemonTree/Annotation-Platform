"""Independent model repository with GPU-specific variants."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision='026'
down_revision='025'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('model_repository_items',
        sa.Column('id',pg.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('user_id',sa.Integer(),nullable=False),
        sa.Column('run_id',pg.UUID(as_uuid=True),sa.ForeignKey('training_runs.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('name',sa.String(100),nullable=False),sa.Column('version',sa.String(100),nullable=False),
        sa.Column('format',sa.String(20),nullable=False),sa.Column('gpu_model',sa.String(200),nullable=False),
        sa.Column('tensorrt_version',sa.String(100),nullable=False),sa.Column('entry_file',sa.String(500),nullable=False),
        sa.Column('file_size',sa.BigInteger(),nullable=False),sa.Column('files',pg.JSONB(),nullable=False),
        sa.Column('source',pg.JSONB(),nullable=False),sa.Column('status',sa.String(20),nullable=False),
        sa.Column('error',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('tenant_id','name','version','format','gpu_model',name='uq_repository_model_variant'))
    op.create_index('ix_model_repository_items_tenant_id','model_repository_items',['tenant_id'])


def downgrade():
    raise RuntimeError('模型仓库文件和版本追溯不得自动删除')
