"""Add resumable image/archive transport without changing existing business rows."""
from alembic import op
import sqlalchemy as sa
revision='012'
down_revision='011'
branch_labels=None
depends_on=None

def upgrade():
    # Freeze 012 so future ORM columns are added only by their own migration.
    bind=op.get_bind()
    if not sa.inspect(bind).has_table('image_uploads'):
        op.execute('''CREATE TABLE image_uploads (
            id UUID PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            collection_id UUID NOT NULL REFERENCES image_collections(id) ON DELETE CASCADE,
            filename VARCHAR(255) NOT NULL, size BIGINT NOT NULL, chunk_hashes JSONB NOT NULL,
            resume_key VARCHAR(64) NOT NULL, min_long_edge INTEGER NOT NULL,
            status VARCHAR(20) DEFAULT 'uploading' NOT NULL,
            cursor INTEGER DEFAULT 0 NOT NULL, attempt INTEGER DEFAULT 0 NOT NULL,
            unique_count INTEGER DEFAULT 0 NOT NULL, result JSONB NOT NULL, error VARCHAR(500),
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''')
        op.execute('CREATE INDEX ix_upload_resume ON image_uploads (tenant_id,user_id,resume_key,created_at)')
        op.execute('CREATE INDEX ix_upload_status ON image_uploads (status,updated_at)')
        op.execute("CREATE UNIQUE INDEX uq_upload_active ON image_uploads (tenant_id,user_id,resume_key) WHERE status != 'completed'")
    if not sa.inspect(bind).has_table('image_upload_items'):
        op.execute('''CREATE TABLE image_upload_items (
            upload_id UUID NOT NULL REFERENCES image_uploads(id) ON DELETE CASCADE,
            image_id UUID NOT NULL REFERENCES images(id) ON DELETE CASCADE,
            PRIMARY KEY (upload_id,image_id))''')

def downgrade():
    raise RuntimeError('Upload progress contains user data; refusing destructive downgrade')
