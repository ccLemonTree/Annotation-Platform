"""Non-destructive annotation integrity, evidence and frozen export references."""
from alembic import op
import sqlalchemy as sa
from models import Annotation, AnnotationEvent, ExportSnapshot

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    for sql in ('SELECT 1 FROM annotations WHERE is_latest GROUP BY asset_id HAVING count(*) > 1 LIMIT 1',
                'SELECT 1 FROM annotations GROUP BY asset_id, version HAVING count(*) > 1 LIMIT 1'):
        if conn.execute(sa.text(sql)).first():
            raise RuntimeError('Existing annotation versions require review; refusing to discard history')
    for index in Annotation.__table__.indexes:
        if index.name in ('uq_annotation_latest','uq_annotation_asset_version'):
            index.create(conn, checkfirst=True)
    AnnotationEvent.__table__.create(conn, checkfirst=True)
    ExportSnapshot.__table__.create(conn, checkfirst=True)

def downgrade():
    raise RuntimeError('Annotation history and exports contain user data; review before destructive downgrade')
