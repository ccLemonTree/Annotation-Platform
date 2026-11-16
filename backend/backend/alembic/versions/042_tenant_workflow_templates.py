"""Move project workflow templates into tenant management."""
from collections import defaultdict

from alembic import op
import sqlalchemy as sa

revision = '042'
down_revision = '041'
branch_labels = None
depends_on = None


def upgrade():
    table = 'project_workflow_templates'
    op.add_column(table, sa.Column('tenant_id', sa.BigInteger(), nullable=True))
    op.execute(f'UPDATE {table} t SET tenant_id=p.tenant_id FROM projects p WHERE p.id=t.project_id')
    conn = op.get_bind()
    rows = conn.execute(sa.text(f'SELECT id, tenant_id, name FROM {table} ORDER BY tenant_id, name, id')).all()
    used = defaultdict(set)
    for row in rows:
        used[row.tenant_id].add(row.name)
    seen = set()
    for row in rows:
        key = (row.tenant_id, row.name)
        if key not in seen:
            seen.add(key)
            continue
        number = 2
        while True:
            suffix = f' ({number})'
            name = row.name[:100-len(suffix)] + suffix
            if name not in used[row.tenant_id]:
                break
            number += 1
        used[row.tenant_id].add(name)
        conn.execute(sa.text(f'UPDATE {table} SET name=:name WHERE id=:id'), {'name':name,'id':row.id})
    op.alter_column(table, 'tenant_id', nullable=False)
    op.create_foreign_key('fk_workflow_template_tenant',table,'tenants',['tenant_id'],['id'],ondelete='CASCADE')
    op.create_unique_constraint('uq_workflow_template_tenant_name',table,['tenant_id','name'])
    op.drop_column(table, 'project_id')


def downgrade():
    raise RuntimeError('Tenant templates no longer have an owning project. Restore the pre-migration template backup to downgrade.')
