"""Complete schema with UUID and JSONB support

Revision ID: 003
Revises: 002
Create Date: 2026-10-12 16:20:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """升级数据库结构以支持UUID和JSONB"""

    # 1. 启用UUID扩展
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # 2. 创建Platform表（SmartEye平台配置）
    op.create_table('platforms',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('platform_name', sa.String(length=100), nullable=False),
        sa.Column('plat_id', sa.String(length=100), nullable=False),
        sa.Column('api_endpoint', sa.String(length=500), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('auth_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform_name'),
        sa.UniqueConstraint('plat_id')
    )
    op.create_index('idx_platform_enabled', 'platforms', ['enabled'])

    # 3. 创建AlarmType表（告警类型）
    op.create_table('alarm_types',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('platform_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('alarm_name', sa.String(length=100), nullable=False),
        sa.Column('alarm_code', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['platform_id'], ['platforms.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_alarm_type_platform', 'alarm_types', ['platform_id'])

    # 4. 更新SmartEyeTask表结构
    # 先检查表是否存在，如果存在则删除重建
    op.drop_table('smarteye_downloads')
    op.drop_table('smarteye_tasks')

    op.create_table('smarteye_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('task_name', sa.String(length=200), nullable=True),
        sa.Column('platform_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('query_params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(length=50), server_default='pending', nullable=False),
        sa.Column('progress', sa.Integer(), server_default='0', nullable=True),
        sa.Column('total_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('downloaded_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('failed_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('storage_prefix', sa.String(length=500), nullable=True),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['platform_id'], ['platforms.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_smarteye_task_status', 'smarteye_tasks', ['status'])
    op.create_index('idx_smarteye_task_platform', 'smarteye_tasks', ['platform_id'])

    # 5. 重建SmartEyeDownload表
    op.create_table('smarteye_downloads',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_id', sa.String(length=200), nullable=False),
        sa.Column('platform', sa.String(length=100), nullable=True),
        sa.Column('device_name', sa.String(length=200), nullable=True),
        sa.Column('device_id', sa.String(length=200), nullable=True),
        sa.Column('target_type', sa.String(length=100), nullable=True),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('blob_path', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('original_url', sa.Text(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('format', sa.String(length=20), nullable=True),
        sa.Column('download_status', sa.String(length=50), server_default='pending', nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['smarteye_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_smarteye_download_task', 'smarteye_downloads', ['task_id'])
    op.create_index('idx_smarteye_download_hash', 'smarteye_downloads', ['content_hash'])
    op.create_index('idx_smarteye_download_status', 'smarteye_downloads', ['download_status'])

    # 6. 创建ImportBatch表（导入批次）
    op.create_table('import_batches',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('source_name', sa.String(length=200), nullable=True),
        sa.Column('source_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('total_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('success_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('failed_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 7. 更新Projects表以支持UUID和新字段
    # 先备份现有数据（如果需要）
    # 然后删除并重建（注意顺序：先删除依赖表）
    op.execute('DROP TABLE IF EXISTS annotations CASCADE')
    op.execute('DROP TABLE IF EXISTS assets CASCADE')
    op.execute('DROP TABLE IF EXISTS training_metrics CASCADE')
    op.execute('DROP TABLE IF EXISTS models CASCADE')  # models依赖training_tasks，所以先删除
    op.execute('DROP TABLE IF EXISTS training_tasks CASCADE')
    op.execute('DROP TABLE IF EXISTS projects CASCADE')

    # 删除旧的ENUM类型（如果存在）
    op.execute('DROP TYPE IF EXISTS tasktype CASCADE')
    op.execute('DROP TYPE IF EXISTS projectstatus CASCADE')
    op.execute('DROP TYPE IF EXISTS annotationstatus CASCADE')
    op.execute('DROP TYPE IF EXISTS reviewstatus CASCADE')

    op.create_table('projects',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('project_name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('task_type', sa.Enum('detection', 'classification', 'segmentation', name='tasktype'), nullable=False),
        sa.Column('label_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.Enum('draft', 'active', 'completed', 'archived', name='projectstatus'), server_default='draft', nullable=True),
        sa.Column('total_assets', sa.Integer(), server_default='0', nullable=True),
        sa.Column('annotated_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('reviewed_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_project_tenant', 'projects', ['tenant_id'])
    op.create_index('idx_project_status', 'projects', ['status'])

    # 添加project_id外键到import_batches
    op.create_foreign_key('fk_import_batch_project', 'import_batches', 'projects', ['project_id'], ['id'], ondelete='CASCADE')

    # 8. 重建Assets表
    op.create_table('assets',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('batch_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('source_record_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('blob_path', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('original_name', sa.String(length=500), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('format', sa.String(length=20), nullable=True),
        sa.Column('capture_time', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('device_info', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('location', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('quality_score', sa.Integer(), nullable=True),
        sa.Column('quality_metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('annotation_status', sa.String(length=50), server_default='pending', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_asset_project', 'assets', ['project_id'])
    op.create_index('idx_asset_batch', 'assets', ['batch_id'])
    op.create_index('idx_asset_status', 'assets', ['annotation_status'])
    op.create_index('idx_asset_hash', 'assets', ['content_hash'])

    # 9. 重建Annotations表
    op.create_table('annotations',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('asset_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('annotation_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=True),
        sa.Column('is_latest', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('annotation_source', sa.String(length=50), nullable=True),
        sa.Column('annotator_id', sa.BigInteger(), nullable=True),
        sa.Column('review_status', sa.String(length=50), server_default='pending', nullable=True),
        sa.Column('reviewer_id', sa.BigInteger(), nullable=True),
        sa.Column('review_comment', sa.Text(), nullable=True),
        sa.Column('reviewed_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['annotator_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_annotation_asset', 'annotations', ['asset_id'])
    op.create_index('idx_annotation_annotator', 'annotations', ['annotator_id'])
    op.create_index('idx_annotation_status', 'annotations', ['review_status'])

    # 10. 创建Dataset表
    op.create_table('datasets',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('dataset_name', sa.String(length=200), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('split_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('asset_ids', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column('export_format', sa.String(length=50), nullable=True),
        sa.Column('export_path', sa.String(length=500), nullable=True),
        sa.Column('exported_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_dataset_project', 'datasets', ['project_id'])

    print("✅ 数据库结构升级完成：支持UUID和JSONB")


def downgrade() -> None:
    """降级到之前的版本"""
    op.drop_table('datasets')
    op.drop_table('annotations')
    op.drop_table('assets')
    op.drop_table('import_batches')
    op.drop_table('alarm_types')
    op.drop_table('smarteye_downloads')
    op.drop_table('smarteye_tasks')
    op.drop_table('platforms')

    # 执行反向SQL会很复杂，建议备份数据后重新执行001和002迁移
    print("⚠️ 降级完成，建议重新执行001和002迁移")
