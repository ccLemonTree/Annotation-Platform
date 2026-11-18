"""Persist managed WeMM instances and the test index coverage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '055'
down_revision = '054'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vector_test_models',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('training_runs.id'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('source', JSONB(), nullable=False),
        sa.Column('server', JSONB(), nullable=False),
        sa.Column('gpu', sa.String(80), unique=True),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('error', sa.Text()),
    )
    op.create_index('ix_vector_test_models_tenant_id', 'vector_test_models', ['tenant_id'])
    op.create_table(
        'vector_test_indexes',
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('model_id', sa.Uuid(), sa.ForeignKey('vector_test_models.id'), primary_key=True),
    )
    op.create_table(
        'vector_test_items',
        sa.Column('project_id', sa.Uuid(), primary_key=True),
        sa.Column('model_id', sa.Uuid(), primary_key=True),
        sa.Column('asset_id', sa.Uuid(), sa.ForeignKey('assets.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('content_hash', sa.String(128), nullable=False),
        sa.Column('error', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ('project_id', 'model_id'), ('vector_test_indexes.project_id', 'vector_test_indexes.model_id'),
            ondelete='CASCADE',
        ),
    )


def downgrade():
    for table in ('vector_test_items', 'vector_test_indexes', 'vector_test_models'):
        op.drop_table(table)
