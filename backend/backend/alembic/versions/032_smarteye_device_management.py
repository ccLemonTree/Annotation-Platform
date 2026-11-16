"""Add platform checks, device scenes and device capture schedules without moving images."""
from alembic import op
import sqlalchemy as sa

revision = '032'
down_revision = '031'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('platforms', sa.Column('managed', sa.Boolean(), nullable=False,
                                       server_default=sa.false()), schema='smarteye')
    for name, keys in (
        ('smarteye_platform_status', [('tenant_id', sa.Integer()), ('plat_id', sa.String(160))]),
        ('smarteye_device_registry', [('tenant_id', sa.Integer()), ('plat_id', sa.String(160)),
                                      ('device_id', sa.String(160))]),
    ):
        op.create_table(name, *(sa.Column(key, kind, primary_key=True) for key, kind in keys),
                        sa.Column('payload', sa.JSON(), nullable=False))
    op.create_table('smarteye_capture_schedules',
                    sa.Column('sched_id', sa.String(160), primary_key=True),
                    sa.Column('tenant_id', sa.Integer(), nullable=False),
                    sa.Column('payload', sa.JSON(), nullable=False))


def downgrade():
    connection = op.get_bind()
    for table in ('smarteye_platform_status', 'smarteye_device_registry', 'smarteye_capture_schedules'):
        if connection.scalar(sa.text(f'SELECT count(*) FROM {table}')):
            raise RuntimeError('已有平台或设备管理记录，请保留数据并回退代码')
    if connection.scalar(sa.text("SELECT count(*) FROM smarteye.platforms WHERE managed OR state='deleted'")):
        raise RuntimeError('已有人工平台配置，请保留字段并回退代码')
    for table in ('smarteye_capture_schedules', 'smarteye_device_registry', 'smarteye_platform_status'):
        op.drop_table(table)
    op.drop_column('platforms', 'managed', schema='smarteye')
