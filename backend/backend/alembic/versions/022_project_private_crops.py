"""Detach project crop assets while preserving IDs, annotations and original files."""
from alembic import op

revision='022'
down_revision='021'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""UPDATE assets AS a SET image_id=NULL,source_type='model_crop'
        FROM images AS i WHERE a.image_id=i.id AND i.source_type='model_crop'""")
    op.execute("""UPDATE projects SET label_schema=COALESCE(label_schema,'{}'::jsonb)||'{"annotation_scope":"crops"}'::jsonb
        WHERE NOT (COALESCE(label_schema,'{}'::jsonb) ? 'annotation_scope')
        AND EXISTS (SELECT 1 FROM assets WHERE assets.project_id=projects.id AND assets.source_type='model_crop')""")


def downgrade():
    raise RuntimeError('保留项目私有裁剪及标注，不自动恢复图库共享关系')
