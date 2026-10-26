"""Reusable YOLO annotation sources, independent of project annotations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='020'
down_revision='019'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('image_uploads',sa.Column('annotation_format',sa.String(20),nullable=True))
    op.add_column('image_uploads',sa.Column('trust_annotations',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.add_column('images',sa.Column('imported_annotation',postgresql.JSONB(),nullable=True))


def downgrade():
    raise RuntimeError('保留导入目标，禁止自动删除续传记录字段')
