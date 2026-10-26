"""初始化数据库迁移

Revision ID: 001
Revises:
Create Date: 2026-10-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建所有表"""

    # 创建租户表
    op.create_table('tenants',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_name', sa.String(length=200), nullable=False),
        sa.Column('contact_email', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_tenant_name', 'tenants', ['tenant_name'])
    op.create_index('idx_tenant_active', 'tenants', ['is_active'])

    # 创建项目表
    op.create_table('projects',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('project_name', sa.String(length=200), nullable=False),
        sa.Column('task_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('label_schema', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('total_assets', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('annotated_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reviewed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_project_tenant', 'projects', ['tenant_id'])
    op.create_index('idx_project_status', 'projects', ['status'])
    op.create_index('idx_project_task_type', 'projects', ['task_type'])

    # 创建SmartEye任务表
    op.create_table('smarteye_tasks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('task_name', sa.String(length=200), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('keyword', sa.String(length=500), nullable=False),
        sa.Column('target_count', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('downloaded_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_smarteye_task_tenant', 'smarteye_tasks', ['tenant_id'])
    op.create_index('idx_smarteye_task_status', 'smarteye_tasks', ['status'])
    op.create_index('idx_smarteye_task_platform', 'smarteye_tasks', ['platform'])

    # 创建SmartEye下载记录表
    op.create_table('smarteye_downloads',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.BigInteger(), nullable=False),
        sa.Column('blob_path', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=100), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('reference_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('blob_path')
    )
    op.create_index('idx_smarteye_download_task', 'smarteye_downloads', ['task_id'])
    op.create_index('idx_smarteye_download_blob', 'smarteye_downloads', ['blob_path'])
    op.create_index('idx_smarteye_download_hash', 'smarteye_downloads', ['content_hash'])

    # 创建资源表
    op.create_table('assets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('blob_path', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=100), nullable=False),
        sa.Column('source_type', sa.String(length=20), nullable=False),
        sa.Column('source_record_id', sa.BigInteger(), nullable=True),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('annotation_status', sa.String(length=20), nullable=False),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_asset_project', 'assets', ['project_id'])
    op.create_index('idx_asset_source', 'assets', ['source_type', 'source_record_id'])
    op.create_index('idx_asset_status', 'assets', ['annotation_status'])
    op.create_index('idx_asset_hash', 'assets', ['content_hash'])

    # 创建标注表
    op.create_table('annotations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('asset_id', sa.BigInteger(), nullable=False),
        sa.Column('annotation_data', sa.Text(), nullable=False),
        sa.Column('annotator_id', sa.BigInteger(), nullable=True),
        sa.Column('annotator_name', sa.String(length=100), nullable=True),
        sa.Column('reviewer_id', sa.BigInteger(), nullable=True),
        sa.Column('reviewer_name', sa.String(length=100), nullable=True),
        sa.Column('review_status', sa.String(length=20), nullable=True),
        sa.Column('review_comment', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_annotation_asset', 'annotations', ['asset_id'])
    op.create_index('idx_annotation_annotator', 'annotations', ['annotator_id'])
    op.create_index('idx_annotation_review_status', 'annotations', ['review_status'])

    # 创建训练任务表
    op.create_table('training_tasks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('task_name', sa.String(length=200), nullable=False),
        sa.Column('framework', sa.String(length=50), nullable=False),
        sa.Column('model_type', sa.String(length=100), nullable=False),
        sa.Column('config_yaml', sa.Text(), nullable=False),
        sa.Column('dataset_version', sa.String(length=50), nullable=False),
        sa.Column('gpu_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('gpu_type', sa.String(length=50), nullable=False, server_default='T4'),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('k8s_job_name', sa.String(length=200), nullable=True),
        sa.Column('k8s_namespace', sa.String(length=100), nullable=False, server_default='training'),
        sa.Column('best_metric', sa.Float(), nullable=True),
        sa.Column('final_loss', sa.Float(), nullable=True),
        sa.Column('model_path', sa.String(length=500), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_training_task_project', 'training_tasks', ['project_id'])
    op.create_index('idx_training_task_status', 'training_tasks', ['status'])

    # 创建训练指标表
    op.create_table('training_metrics',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.BigInteger(), nullable=False),
        sa.Column('epoch', sa.Integer(), nullable=False),
        sa.Column('step', sa.Integer(), nullable=False),
        sa.Column('loss', sa.Float(), nullable=False),
        sa.Column('accuracy', sa.Float(), nullable=True),
        sa.Column('learning_rate', sa.Float(), nullable=True),
        sa.Column('extra_metrics', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['training_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_training_metric_task', 'training_metrics', ['task_id'])
    op.create_index('idx_training_metric_epoch', 'training_metrics', ['task_id', 'epoch'])

    # 创建模型表
    op.create_table('models',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('training_task_id', sa.BigInteger(), nullable=True),
        sa.Column('model_name', sa.String(length=200), nullable=False),
        sa.Column('model_version', sa.String(length=50), nullable=False),
        sa.Column('model_type', sa.String(length=100), nullable=False),
        sa.Column('framework', sa.String(length=50), nullable=False),
        sa.Column('model_path', sa.String(length=500), nullable=False),
        sa.Column('model_size', sa.BigInteger(), nullable=False),
        sa.Column('accuracy', sa.Float(), nullable=True),
        sa.Column('f1_score', sa.Float(), nullable=True),
        sa.Column('inference_time', sa.Float(), nullable=True),
        sa.Column('is_deployed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('deployment_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['training_task_id'], ['training_tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_model_project', 'models', ['project_id'])
    op.create_index('idx_model_deployed', 'models', ['is_deployed'])

    print("✅ 数据库表结构创建完成")


def downgrade() -> None:
    """删除所有表"""
    op.drop_table('models')
    op.drop_table('training_metrics')
    op.drop_table('training_tasks')
    op.drop_table('annotations')
    op.drop_table('assets')
    op.drop_table('smarteye_downloads')
    op.drop_table('smarteye_tasks')
    op.drop_table('projects')
    op.drop_table('tenants')
    print("⚠️  数据库表已删除")
