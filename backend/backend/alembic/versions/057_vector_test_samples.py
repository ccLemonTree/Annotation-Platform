"""Keep project retrieval test samples separate from business images."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '057'
down_revision = '056'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('vector_test_indexes', sa.Column('legacy_enabled', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column('vector_test_indexes', sa.Column('isolated_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute('UPDATE vector_test_indexes SET isolated_enabled = true')
    op.create_table('vector_test_datasets',
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('detection', JSONB(), nullable=False))
    op.create_table('vector_test_samples',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('parent_id', sa.Uuid(), sa.ForeignKey('vector_test_samples.id', ondelete='CASCADE')),
        sa.Column('blob_path', sa.String(500), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('original_name', sa.String(255), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('format', sa.String(20), nullable=False),
        sa.Column('source_type', sa.String(30), nullable=False),
        sa.Column('device_name', sa.String(255)),
        sa.Column('detection_revision', sa.Uuid()),
        sa.Column('detection_error', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('project_id', 'blob_path'))
    op.create_index('ix_vector_test_samples_project_id', 'vector_test_samples', ['project_id'])
    op.create_table('vector_test_sample_items',
        sa.Column('project_id', sa.Uuid(), primary_key=True),
        sa.Column('model_id', sa.Uuid(), primary_key=True),
        sa.Column('asset_id', sa.Uuid(), sa.ForeignKey('vector_test_samples.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('content_hash', sa.String(128), nullable=False),
        sa.Column('error', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(('project_id', 'model_id'),
            ('vector_test_indexes.project_id', 'vector_test_indexes.model_id'), ondelete='CASCADE'))


def downgrade():
    for table in ('vector_test_sample_items', 'vector_test_samples', 'vector_test_datasets'):
        op.drop_table(table)
    op.execute('DELETE FROM vector_test_indexes WHERE NOT legacy_enabled')
    op.drop_column('vector_test_indexes', 'isolated_enabled')
    op.drop_column('vector_test_indexes', 'legacy_enabled')
