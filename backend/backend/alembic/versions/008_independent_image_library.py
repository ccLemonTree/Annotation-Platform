"""Backfill independent originals without changing project-use or annotation IDs."""
from alembic import op
import sqlalchemy as sa
from models import LibraryImage

revision='008'
down_revision='007'
branch_labels=None
depends_on=None

def upgrade():
    conn=op.get_bind()
    LibraryImage.__table__.create(conn,checkfirst=True)
    if 'image_id' not in {c['name'] for c in sa.inspect(conn).get_columns('assets')}:
        op.add_column('assets',sa.Column('image_id',sa.UUID(),nullable=True))
        op.create_foreign_key('fk_asset_image','assets','images',['image_id'],['id'],ondelete='RESTRICT')
        op.create_index('ix_assets_image_id','assets',['image_id'])
    conn.execute(sa.text('''INSERT INTO images
        (id,tenant_id,blob_path,content_hash,original_name,file_size,width,height,format,source_type,
         source_record_id,batch_id,capture_time,device_info,location,quality_score,quality_metrics,created_at)
        SELECT DISTINCT ON(p.tenant_id,a.content_hash) a.id,p.tenant_id,a.blob_path,a.content_hash,
         a.original_name,a.file_size,a.width,a.height,a.format,a.source_type,a.source_record_id,
         a.batch_id::text,a.capture_time,a.device_info,a.location,a.quality_score,a.quality_metrics,a.created_at
        FROM assets a JOIN projects p ON p.id=a.project_id
        ORDER BY p.tenant_id,a.content_hash,a.created_at,a.id
        ON CONFLICT(tenant_id,content_hash) DO NOTHING'''))
    conn.execute(sa.text('''UPDATE assets a SET image_id=i.id FROM projects p,images i
        WHERE a.project_id=p.id AND i.tenant_id=p.tenant_id AND i.content_hash=a.content_hash'''))
    conn.execute(sa.text('''INSERT INTO images
        (id,tenant_id,blob_path,content_hash,original_name,file_size,width,height,format,source_type,source_record_id,batch_id)
        SELECT DISTINCT ON(j.tenant_id,f.content_hash) f.id::uuid,j.tenant_id,f.blob_path,f.content_hash,
         f.metadata->>'original_name',(f.metadata->>'file_size')::integer,
         (f.metadata->>'width')::integer,(f.metadata->>'height')::integer,f.metadata->>'format',
         'smarteye',f.id::uuid,f.task_id
        FROM smarteye_files f JOIN smarteye_jobs j ON j.task_id=f.task_id
        ORDER BY j.tenant_id,f.content_hash,f.id
        ON CONFLICT(tenant_id,content_hash) DO NOTHING'''))

def downgrade():
    raise RuntimeError('Independent images contain user data; refusing destructive downgrade')
