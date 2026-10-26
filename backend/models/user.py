"""
用户模型
"""

from sqlalchemy import Column, String, Boolean, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from enum import Enum

from .base import Base, UUIDMixin, TimestampMixin


class UserRole(str, Enum):
    """用户角色"""
    ADMIN = "admin"          # 系统管理员
    TENANT_ADMIN = "tenant_admin"  # 租户管理员
    ANNOTATOR = "annotator"  # 标注员
    REVIEWER = "reviewer"    # 复核员
    VIEWER = "viewer"        # 查看者


class User(Base, UUIDMixin, TimestampMixin):
    """用户表"""
    __tablename__ = "users"

    username = Column(String(50), unique=True, nullable=False, comment="用户名")
    email = Column(String(100), unique=True, nullable=False, comment="邮箱")
    hashed_password = Column(String(255), nullable=False, comment="密码哈希")

    full_name = Column(String(100), comment="真实姓名")
    phone = Column(String(20), comment="电话")

    role = Column(SQLEnum(UserRole), default=UserRole.ANNOTATOR, comment="角色")
    is_active = Column(Boolean, default=True, comment="是否激活")

    # 租户关联
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # 关系
    tenant = relationship("Tenant", back_populates="users")
    annotations = relationship("Annotation", back_populates="annotator")

    def __repr__(self):
        return f"<User {self.username}({self.role})>"
