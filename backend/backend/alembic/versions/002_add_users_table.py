"""add users table and update relationships

Revision ID: 002
Revises: 001
Create Date: 2026-10-12 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建 users 表
    op.create_table('users',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False, comment='租户ID'),
        sa.Column('username', sa.String(length=100), nullable=False, comment='用户名'),
        sa.Column('email', sa.String(length=255), nullable=False, comment='邮箱'),
        sa.Column('password_hash', sa.String(length=255), nullable=False, comment='密码哈希'),
        sa.Column('full_name', sa.String(length=100), nullable=True, comment='全名'),
        sa.Column('role', sa.String(length=50), nullable=False, comment='角色: admin/annotator/reviewer'),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False, comment='是否激活'),
        sa.Column('last_login_at', sa.DateTime(), nullable=True, comment='最后登录时间'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('tenant_id', 'username', name='uq_user_tenant_username'),
        sa.UniqueConstraint('tenant_id', 'email', name='uq_user_tenant_email'),
        comment='用户表'
    )
    op.create_index('idx_user_tenant', 'users', ['tenant_id'])
    op.create_index('idx_user_email', 'users', ['email'])
    op.create_index('idx_user_role', 'users', ['role'])

    # 为 projects 表添加 tenant_id 外键（如果还没有的话）
    # 注意：这里假设 projects 表已经有 tenant_id 列但没有外键约束
    try:
        op.create_foreign_key('fk_project_tenant', 'projects', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    except Exception:
        pass  # 如果外键已存在则跳过

    # 为 annotations 表添加 annotator 外键（如果 annotator_id 列存在）
    # 先检查列是否存在，如果不存在则跳过
    try:
        op.create_foreign_key('fk_annotation_annotator', 'annotations', 'users', ['annotator_id'], ['id'], ondelete='SET NULL')
    except Exception:
        pass

    try:
        op.create_foreign_key('fk_annotation_reviewer', 'annotations', 'users', ['reviewer_id'], ['id'], ondelete='SET NULL')
    except Exception:
        pass


def downgrade() -> None:
    # 删除外键
    try:
        op.drop_constraint('fk_annotation_reviewer', 'annotations', type_='foreignkey')
    except Exception:
        pass

    try:
        op.drop_constraint('fk_annotation_annotator', 'annotations', type_='foreignkey')
    except Exception:
        pass

    try:
        op.drop_constraint('fk_project_tenant', 'projects', type_='foreignkey')
    except Exception:
        pass

    # 删除索引
    op.drop_index('idx_user_role', table_name='users')
    op.drop_index('idx_user_email', table_name='users')
    op.drop_index('idx_user_tenant', table_name='users')

    # 删除 users 表
    op.drop_table('users')
