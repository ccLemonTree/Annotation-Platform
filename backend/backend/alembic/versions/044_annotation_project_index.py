"""Store annotation ownership directly and index each project's review queue."""
from alembic import op
import sqlalchemy as sa

revision = '044'
down_revision = '043'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('annotations',sa.Column('project_id',sa.Uuid(),nullable=True))
    op.execute('UPDATE annotations a SET project_id=s.project_id FROM assets s WHERE a.asset_id=s.id')
    op.alter_column('annotations','project_id',nullable=False)
    op.create_foreign_key('fk_annotations_project','annotations','projects',['project_id'],['id'],ondelete='CASCADE')
    op.create_index('ix_annotations_project_latest','annotations',
        ['project_id',sa.text('created_at DESC'),sa.text('version DESC'),'id'],postgresql_where=sa.text('is_latest = true'))
    op.create_index('ix_annotations_project_review','annotations',
        ['project_id','review_status',sa.text('created_at DESC'),sa.text('version DESC'),'id'],postgresql_where=sa.text('is_latest = true'))


def downgrade():
    op.drop_index('ix_annotations_project_review',table_name='annotations')
    op.drop_index('ix_annotations_project_latest',table_name='annotations')
    op.drop_constraint('fk_annotations_project','annotations',type_='foreignkey')
    op.drop_column('annotations','project_id')
