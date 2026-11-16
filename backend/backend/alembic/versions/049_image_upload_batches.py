"""Persist upload batches and each file's latest result."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision='049'
down_revision='048'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('image_upload_batches',
        sa.Column('id',UUID(as_uuid=True),primary_key=True),
        sa.Column('tenant_id',sa.BigInteger(),sa.ForeignKey('tenants.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('project_id',UUID(as_uuid=True),sa.ForeignKey('projects.id',ondelete='CASCADE')),
        sa.Column('collection_name',sa.String(200),nullable=False),
        sa.Column('image_kind',sa.String(12),nullable=False),
        sa.Column('min_long_edge',sa.Integer()),
        sa.Column('annotation_format',sa.String(20)),
        sa.Column('trust_annotations',sa.Boolean(),server_default=sa.text('false'),nullable=False),
        sa.Column('total',sa.Integer(),nullable=False),
        sa.Column('manifest',sa.String(64)),
        sa.Column('created_at',sa.DateTime(),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_upload_batches_project','image_upload_batches',['tenant_id','project_id','created_at'])
    op.create_table('image_upload_batch_files',
        sa.Column('batch_id',UUID(as_uuid=True),sa.ForeignKey('image_upload_batches.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('file_key',sa.String(80),primary_key=True),
        sa.Column('upload_id',UUID(as_uuid=True),sa.ForeignKey('image_uploads.id',ondelete='SET NULL')),
        sa.Column('status',sa.String(20),nullable=False))
    op.create_index('ix_image_upload_batch_files_upload_id','image_upload_batch_files',['upload_id'])


def downgrade():
    op.drop_table('image_upload_batch_files')
    op.drop_table('image_upload_batches')
