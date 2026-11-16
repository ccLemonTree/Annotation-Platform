"""Backfill compact frozen-version metadata for list queries."""
from alembic import op

revision = '053'
down_revision = '052'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE export_snapshots ADD COLUMN IF NOT EXISTS list_metadata JSONB')
    op.execute("""
        UPDATE export_snapshots
        SET list_metadata = jsonb_build_object(
            'training', manifest->'training',
            'version_label', manifest->'version_label',
            'asset_count', CASE WHEN jsonb_typeof(manifest->'assets') = 'array'
                                THEN jsonb_array_length(manifest->'assets') ELSE 0 END
        )
    """)


def downgrade():
    op.drop_column('export_snapshots', 'list_metadata')
