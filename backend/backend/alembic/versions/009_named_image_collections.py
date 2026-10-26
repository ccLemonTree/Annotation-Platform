"""Add named directory groups; existing images and project annotations stay intact."""
from alembic import op
import sqlalchemy as sa
from models import ImageCollection,ImageCollectionMember

revision='009'
down_revision='008'
branch_labels=None
depends_on=None

def upgrade():
    conn=op.get_bind()
    ImageCollection.__table__.create(conn,checkfirst=True)
    ImageCollectionMember.__table__.create(conn,checkfirst=True)
    conn.execute(sa.text('''INSERT INTO image_collections(id,tenant_id,name)
        SELECT DISTINCT ON(tenant_id) id,tenant_id,'历史图片' FROM images ORDER BY tenant_id,id
        ON CONFLICT(tenant_id,name) DO NOTHING'''))
    conn.execute(sa.text('''INSERT INTO image_collection_members(collection_id,image_id)
        SELECT c.id,i.id FROM images i JOIN image_collections c ON c.tenant_id=i.tenant_id AND c.name='历史图片'
        WHERE NOT EXISTS(SELECT 1 FROM image_collection_members m WHERE m.image_id=i.id)
        ON CONFLICT(collection_id,image_id) DO NOTHING'''))

def downgrade():
    raise RuntimeError('Image collection names contain user data; refusing destructive downgrade')
