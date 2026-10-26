"""Add durable scene classification; preserve originals and annotation versions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from models.image_ingest import ImageIngestSettings

revision='010'
down_revision='009'
branch_labels=None
depends_on=None

def upgrade():
    ImageIngestSettings.__table__.create(op.get_bind(),checkfirst=True)
    inspector=sa.inspect(op.get_bind())
    existing={column['name'] for column in inspector.get_columns('images')}
    columns=[sa.Column('scene_labels',JSONB,nullable=False,server_default=sa.text("'[]'")),
        sa.Column('scene_status',sa.String(30),nullable=False,server_default='unclassified'),
        sa.Column('scene_error',sa.String(500)),sa.Column('scene_attempt',sa.Integer,nullable=False,server_default='0'),
        sa.Column('scene_metadata',JSONB)]
    # Earlier migrations create images from the current model, so fresh installs may already have these fields.
    for column in columns:
        if column.name not in existing:op.add_column('images',column)
    if 'idx_image_scene_status' not in {index['name'] for index in inspector.get_indexes('images')}:
        op.create_index('idx_image_scene_status','images',['scene_status','created_at'])

def downgrade():
    raise RuntimeError('Scene labels and ingestion settings contain user data; refusing destructive downgrade')
