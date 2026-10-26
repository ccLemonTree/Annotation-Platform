"""Persist evidence snapshots and training analysis reports without changing runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision='023'
down_revision='022'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind();inspector=sa.inspect(bind)
    columns=[sa.Column('id',pg.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('run_id',pg.UUID(as_uuid=True),sa.ForeignKey('training_runs.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.Integer(),nullable=False),sa.Column('evidence',pg.JSONB(),nullable=False),
        sa.Column('model',pg.JSONB(),nullable=False),sa.Column('prompt',sa.Text(),nullable=False),
        sa.Column('analysis',pg.JSONB(),nullable=False),sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False)]
    if inspector.has_table('training_analyses'):
        # A locally verified first report may already have created this exact table.
        actual=inspector.get_columns('training_analyses')
        expected={c.name:(str(c.type.compile(dialect=bind.dialect)),c.nullable) for c in columns}
        if {c['name']:(str(c['type'].compile(dialect=bind.dialect)),c['nullable']) for c in actual}!=expected:
            raise RuntimeError('023发现不匹配的分析表，禁止覆盖')
        fks=inspector.get_foreign_keys('training_analyses')
        expected_fks={('tenant_id','tenants','id',None),('run_id','training_runs','id','CASCADE')}
        if {(f['constrained_columns'][0],f['referred_table'],f['referred_columns'][0],f['options'].get('ondelete')) for f in fks}!=expected_fks:
            raise RuntimeError('023分析表外键不匹配，禁止覆盖')
        if inspector.get_pk_constraint('training_analyses')['constrained_columns']!=['id']:
            raise RuntimeError('023分析表主键不匹配，禁止覆盖')
    else:op.create_table('training_analyses',*columns)
    if not any(i['name']=='ix_training_analyses_run_id' for i in sa.inspect(bind).get_indexes('training_analyses')):
        op.create_index('ix_training_analyses_run_id','training_analyses',['run_id'])


def downgrade():
    raise RuntimeError('分析报告及证据快照不得自动删除')
