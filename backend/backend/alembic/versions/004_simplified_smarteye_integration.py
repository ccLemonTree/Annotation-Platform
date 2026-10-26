"""Simplified schema for SmartEye integration

Revision ID: 004
Revises: 003
Create Date: 2026-10-12 17:30:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """简化SmartEye集成架构"""

    # 1. 检查表是否存在，然后删除（避免表不存在时报错）
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'smarteye_downloads' in existing_tables:
        op.drop_table('smarteye_downloads')
        print("   - 删除了 smarteye_downloads 表")

    if 'smarteye_tasks' in existing_tables:
        op.drop_table('smarteye_tasks')
        print("   - 删除了 smarteye_tasks 表")

    # 2. 修改Platform表，添加SmartEye连接配置
    op.add_column('platforms',
        sa.Column('platform_type', sa.String(50), server_default='smarteye', nullable=False)
    )
    op.add_column('platforms',
        sa.Column('storage_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )

    # storage_config 示例:
    # {
    #   "type": "local",
    #   "base_path": "/data/smarteye/images",
    #   "is_same_server": true
    # }

    # 3. 为auth_config添加注释（它存储SmartEye的API配置）
    op.execute("""
        COMMENT ON COLUMN platforms.auth_config IS
        'SmartEye API配置，例如: {"api_url": "http://localhost:5000", "timeout": 30}'
    """)

    print("✅ 数据库结构简化完成")
    print("   - 删除了 smarteye_tasks 和 smarteye_downloads 表")
    print("   - Platform表支持多类型数据源")


def downgrade() -> None:
    """回退到之前的版本"""
    op.drop_column('platforms', 'storage_config')
    op.drop_column('platforms', 'platform_type')

    # 注意：无法恢复已删除的表，需要重新创建
    print("⚠️ 降级完成，但smarteye_tasks和smarteye_downloads表需要手动恢复")
