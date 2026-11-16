"""Distinguish immutable TensorRT batch variants and recover known export settings."""
from alembic import op
import sqlalchemy as sa

revision='030'
down_revision='029'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('model_repository_items',sa.Column('batch_profile',sa.String(64),nullable=False,server_default=''))
    connection=op.get_bind()
    rows=connection.execute(sa.text("""SELECT m.id,r.config FROM model_repository_items m JOIN training_runs r ON r.id=m.run_id AND r.tenant_id=m.tenant_id
        WHERE m.format='tensorrt' AND m.source->>'kind'='training' AND r.kind='model_export' AND r.status='completed'""")).mappings().all()
    for row in rows:
        options=row['config'].get('output',{});profile=''
        if not isinstance(options,dict):continue
        if options.get('dynamic_batch') is False and type(options.get('batch')) is int and 1<=options['batch']<=1024:profile='fixed:'+str(options['batch'])
        elif options.get('dynamic_batch') is True:
            values=[options.get(key) for key in ('min_batch','opt_batch','max_batch')]
            if all(type(n) is int and 1<=n<=1024 for n in values) and values==sorted(values):profile='dynamic:'+':'.join(str(n) for n in values)
        if profile:connection.execute(sa.text('UPDATE model_repository_items SET batch_profile=:profile WHERE id=:id'),dict(profile=profile,id=row['id']))
    op.drop_constraint('uq_repository_model_variant','model_repository_items',type_='unique')
    op.create_unique_constraint('uq_repository_model_variant','model_repository_items',['tenant_id','name','version','format','gpu_model','batch_profile'])


def downgrade():
    raise RuntimeError('同版本不同batch不得合并或删除，请保留字段并回退代码')
