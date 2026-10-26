"""Keep one image reference per project and content hash without deleting data."""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    duplicate = op.get_bind().execute(sa.text(
        "SELECT 1 FROM assets GROUP BY project_id, content_hash HAVING count(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError("Duplicate assets require review; migration will not delete or merge existing data")
    op.create_unique_constraint("uq_asset_project_content", "assets", ["project_id", "content_hash"])


def downgrade():
    op.drop_constraint("uq_asset_project_content", "assets", type_="unique")
