"""Add embedded SmartEye without changing existing business rows."""
from alembic import op
from integrations.smarteye.runtime import metadata
from integrations.smarteye.schema import upgrade_schema

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    metadata.create_all(conn)
    upgrade_schema(conn)

def downgrade():
    raise RuntimeError('SmartEye contains user data; export and review before a destructive downgrade')
