"""Allow frozen manifests to be stored alongside their archives."""
from alembic import op

revision = '054'
down_revision = '053'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE export_snapshots ADD COLUMN IF NOT EXISTS manifest_path VARCHAR(500)')
    op.alter_column('export_snapshots', 'manifest', nullable=True)


def downgrade():
    # Restoring the JSON is required before removing the file pointers.
    op.alter_column('export_snapshots', 'manifest', nullable=False)
    op.drop_column('export_snapshots', 'manifest_path')
