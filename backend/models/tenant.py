"""
租户模型
"""

from sqlalchemy import Column, String, Boolean, Integer
from sqlalchemy.orm import relationship

from .base import Base, UUIDMixin, TimestampMixin


class Tenant(Base, UUIDMixin, TimestampMixin):
    """租户表"""
    __tablename__ = "tenants"

    name = Column(String(100), nullable=False, comment="租户名称")
    code = Column(String(50), unique=True, nullable=False, comment="租户代码")
    contact_name = Column(String(50), comment="联系人")
    contact_email = Column(String(100), comment="联系邮箱")
    contact_phone = Column(String(20), comment="联系电话")

    # 配额限制
    max_users = Column(Integer, default=10, comment="最大用户数")
    max_projects = Column(Integer, default=50, comment="最大项目数")
    max_storage_gb = Column(Integer, default=100, comment="最大存储空间(GB)")

    is_active = Column(Boolean, default=True, comment="是否激活")

    # 关系
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="tenant", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Tenant {self.name}({self.code})>"
