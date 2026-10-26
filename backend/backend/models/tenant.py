"""
数据库模型：租户表
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func, BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List

from .base import Base


class Tenant(Base):
    """租户表"""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, comment="租户名称")
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="联系邮箱")
    is_active: Mapped[bool] = mapped_column(default=True, comment="是否激活")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关联关系
    projects: Mapped[List["Project"]] = relationship("Project", back_populates="tenant", cascade="all, delete-orphan")
    users: Mapped[List["User"]] = relationship("User", back_populates="tenant", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        Index('idx_tenant_name', 'tenant_name'),
    )

    def __repr__(self):
        return f"<Tenant(id={self.id}, name={self.tenant_name})>"
