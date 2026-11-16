"""Assign annotation projects to ordinary users."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='034'
down_revision='031'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('project_members',
        sa.Column('project_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('projects.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True))
    op.create_index('ix_project_members_user_id','project_members',['user_id'])


def downgrade():
    op.drop_table('project_members')
