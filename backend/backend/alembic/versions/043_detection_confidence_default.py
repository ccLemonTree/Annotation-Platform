"""Set the registered detection model default to 0.85."""
from alembic import op

revision = '043'
down_revision = '042'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE model_services SET confidence=0.85 WHERE kind='detection' AND confidence<>0.85")


def downgrade():
    # Existing model defaults are editable settings.
    pass
