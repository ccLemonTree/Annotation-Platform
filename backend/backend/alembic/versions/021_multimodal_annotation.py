"""Project classification, descriptions, vector judgments and preprocessing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision='021'
down_revision='020'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'caption'")
    op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'vector'")
    op.create_table('project_work_jobs',
        sa.Column('id',UUID(),primary_key=True),
        sa.Column('project_id',UUID(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('operation',sa.String(20),nullable=False),sa.Column('model',JSONB(),nullable=False),
        sa.Column('options',JSONB(),nullable=False),sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_project_work_jobs_project_id','project_work_jobs',['project_id'])
    op.create_table('project_work_items',
        sa.Column('id',UUID(),primary_key=True),
        sa.Column('job_id',UUID(),sa.ForeignKey('project_work_jobs.id',ondelete='CASCADE'),nullable=False),
        sa.Column('asset_id',UUID(),sa.ForeignKey('assets.id',ondelete='SET NULL')),
        sa.Column('content_hash',sa.String(100),nullable=False),sa.Column('base_version',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),sa.Column('attempt',sa.Integer(),nullable=False),
        sa.Column('result',JSONB()),sa.Column('error',sa.String(500)),
        sa.UniqueConstraint('job_id','asset_id',name='uq_project_work_asset'))
    op.create_index('ix_project_work_items_job_id','project_work_items',['job_id'])
    op.create_index('ix_project_work_claim','project_work_items',['status','job_id','id'])
    op.create_table('match_queries',sa.Column('id',UUID(),primary_key=True),
        sa.Column('project_id',UUID(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('query',JSONB(),nullable=False),sa.Column('space',JSONB(),nullable=False),
        sa.Column('hits',JSONB(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_match_queries_project_id','match_queries',['project_id'])


def downgrade():
    raise RuntimeError('保留项目标注和配对数据，禁止自动删除')
