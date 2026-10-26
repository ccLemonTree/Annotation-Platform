"""Collection policy snapshots; scene results and deduplicated originals stay on images."""
from alembic import op
import sqlalchemy as sa
import json
from sqlalchemy.dialects.postgresql import JSONB
from services.image_ingest import default_config

revision='011'
down_revision='010'
branch_labels=None
depends_on=None

def upgrade():
    conn=op.get_bind();inspector=sa.inspect(conn)
    existing={column['name'] for column in inspector.get_columns('image_collections')}
    for column in (sa.Column('ingest_config',JSONB),sa.Column('ingest_revision',sa.Integer,nullable=False,server_default='1')):
        if column.name not in existing:op.add_column('image_collections',column)
    if 'scene_config' not in {column['name'] for column in inspector.get_columns('images')}:
        op.add_column('images',sa.Column('scene_config',JSONB))
    conn.execute(sa.text('''UPDATE image_collections c SET ingest_config=COALESCE(
        (SELECT s.config FROM image_ingest_settings s WHERE s.tenant_id=c.tenant_id),CAST(:defaults AS jsonb))
        WHERE c.ingest_config IS NULL'''),{'defaults':json.dumps(default_config().model_dump())})
    # Freeze queued legacy rules without reclassifying completed originals.
    conn.execute(sa.text('''UPDATE images i SET scene_config=(SELECT jsonb_build_object(
        'collection_id',c.id::text,'revision',c.ingest_revision,'config',c.ingest_config)
        FROM image_collection_members m JOIN image_collections c ON c.id=m.collection_id
        WHERE m.image_id=i.id AND c.tenant_id=i.tenant_id ORDER BY c.id LIMIT 1)
        WHERE i.scene_config IS NULL AND i.scene_status IN ('pending','processing')'''))

def downgrade():
    raise RuntimeError('Collection policies contain user data; refusing destructive downgrade')
