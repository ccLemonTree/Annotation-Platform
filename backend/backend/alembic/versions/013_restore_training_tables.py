"""Restore missing training tables; refuse incompatible legacy structures without rewriting data."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None

# Freeze revision 013 independently of future ORM changes.
metadata = sa.MetaData()
sa.Table('projects', metadata, sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True))
status = postgresql.ENUM('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', name='trainingstatus')
tasks = sa.Table('training_tasks', metadata,
    sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
    sa.Column('task_name', sa.String(200), nullable=False),
    sa.Column('framework', sa.String(50), nullable=False),
    sa.Column('model_type', sa.String(100), nullable=False),
    sa.Column('config_yaml', sa.Text, nullable=False),
    sa.Column('dataset_version', sa.String(50), nullable=False),
    sa.Column('gpu_count', sa.Integer, nullable=False),
    sa.Column('gpu_type', sa.String(50), nullable=False),
    sa.Column('status', status, nullable=False),
    sa.Column('progress', sa.Integer, nullable=False),
    sa.Column('k8s_job_name', sa.String(200)),
    sa.Column('k8s_namespace', sa.String(100), nullable=False),
    sa.Column('best_metric', sa.Float),
    sa.Column('final_loss', sa.Float),
    sa.Column('model_path', sa.String(500)),
    sa.Column('error_message', sa.Text),
    sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Column('started_at', sa.DateTime),
    sa.Column('completed_at', sa.DateTime),
    sa.Index('idx_training_task_project', 'project_id'),
    sa.Index('idx_training_task_status', 'status'),
    sa.Index('idx_training_task_created', 'created_at'))
metrics = sa.Table('training_metrics', metadata,
    sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column('task_id', sa.BigInteger, sa.ForeignKey('training_tasks.id', ondelete='CASCADE'), nullable=False),
    sa.Column('epoch', sa.Integer, nullable=False),
    sa.Column('step', sa.Integer, nullable=False),
    sa.Column('loss', sa.Float, nullable=False),
    sa.Column('accuracy', sa.Float),
    sa.Column('learning_rate', sa.Float),
    sa.Column('extra_metrics', sa.Text),
    sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Index('idx_training_metric_task', 'task_id'),
    sa.Index('idx_training_metric_epoch', 'task_id', 'epoch'),
    sa.Index('idx_training_metric_created', 'created_at'))
models = sa.Table('models', metadata,
    sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
    sa.Column('training_task_id', sa.BigInteger, sa.ForeignKey('training_tasks.id', ondelete='SET NULL')),
    sa.Column('model_name', sa.String(200), nullable=False),
    sa.Column('model_version', sa.String(50), nullable=False),
    sa.Column('model_type', sa.String(100), nullable=False),
    sa.Column('framework', sa.String(50), nullable=False),
    sa.Column('model_path', sa.String(500), nullable=False),
    sa.Column('model_size', sa.BigInteger, nullable=False),
    sa.Column('accuracy', sa.Float),
    sa.Column('f1_score', sa.Float),
    sa.Column('inference_time', sa.Float),
    sa.Column('is_deployed', sa.Boolean, nullable=False),
    sa.Column('deployment_url', sa.String(500)),
    sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Index('idx_model_project', 'project_id'),
    sa.Index('idx_model_training_task', 'training_task_id'),
    sa.Index('idx_model_deployed', 'is_deployed'),
    sa.Index('idx_model_created', 'created_at'))
TRAINING_TABLES = (tasks, metrics, models)


def incompatible(table, detail):
    raise RuntimeError(f'Incompatible training schema: {table}: {detail}; refusing automatic conversion, review existing data')


def validate_table(conn, inspector, table):
    columns = {c['name']: c for c in inspector.get_columns(table.name, schema='public')}
    if set(columns) != set(table.c.keys()):
        incompatible(table.name, 'column set differs')
    for column in table.c:
        actual = columns[column.name]
        expected_type = column.type.compile(dialect=conn.dialect).replace('FLOAT', 'DOUBLE PRECISION')
        actual_type = actual['type'].compile(dialect=conn.dialect)
        if actual_type != expected_type or actual['nullable'] != column.nullable:
            incompatible(table.name, f'{column.name} type/nullability differs')
        if column.primary_key and not (actual.get('identity') or str(actual.get('default', '')).startswith('nextval(')):
            incompatible(table.name, f'{column.name} has no generated ID default')
        if column.server_default is not None and actual.get('default') != 'now()':
            incompatible(table.name, f'{column.name} timestamp default differs')
    if inspector.get_pk_constraint(table.name, schema='public')['constrained_columns'] != ['id']:
        incompatible(table.name, 'primary key differs')
    actual_keys = {
        (tuple(fk['constrained_columns']), fk['referred_schema'] or 'public', fk['referred_table'],
         tuple(fk['referred_columns']), fk['options'].get('ondelete'))
        for fk in inspector.get_foreign_keys(table.name, schema='public')}
    expected_keys = {
        (tuple(c.name for c in fk.columns), 'public', fk.elements[0].column.table.name,
         tuple(e.column.name for e in fk.elements), fk.ondelete)
        for fk in table.foreign_key_constraints}
    if actual_keys != expected_keys:
        incompatible(table.name, 'foreign keys differ')
    indexes = {i['name']: i for i in inspector.get_indexes(table.name, schema='public')}
    for index in table.indexes:
        actual = indexes.get(index.name)
        if not actual or actual['column_names'] != [c.name for c in index.columns] or actual['unique']:
            incompatible(table.name, f'index {index.name} differs/missing')
    if inspector.get_check_constraints(table.name, schema='public') or any(i['unique'] for i in indexes.values()):
        incompatible(table.name, 'unexpected check/unique constraints')


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'projects' not in inspector.get_table_names(schema='public'):
        incompatible('projects', 'missing UUID projects prerequisite')
    project_id = next((c for c in inspector.get_columns('projects', schema='public') if c['name'] == 'id'), None)
    if not project_id or not isinstance(project_id['type'], sa.UUID):
        incompatible('projects', 'id must be UUID')
    for enum in inspector.get_enums(schema='public'):
        if enum['name'] == 'trainingstatus' and enum['labels'] != status.enums:
            incompatible('trainingstatus', 'enum labels differ')
    existing = set(inspector.get_table_names(schema='public'))
    # Validate every existing table before creating anything, including partial schemas.
    for table in TRAINING_TABLES:
        if table.name in existing:
            validate_table(conn, inspector, table)
    status.create(conn, checkfirst=True)
    for table in TRAINING_TABLES:
        table.create(conn, checkfirst=True)


def downgrade():
    raise RuntimeError('Training tables contain user data; refusing destructive downgrade; export and review first')
