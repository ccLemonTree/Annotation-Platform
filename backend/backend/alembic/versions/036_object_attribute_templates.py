"""Tenant-scoped reusable object attribute templates."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='036'
down_revision='035'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('object_attribute_templates',
        sa.Column('id',postgresql.UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id',ondelete='CASCADE'),nullable=False),
        sa.Column('name',sa.String(60),nullable=False),
        sa.Column('config',postgresql.JSONB(),nullable=False),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('tenant_id','name',name='uq_attribute_template_name'))


def downgrade():
    op.drop_table('object_attribute_templates')
