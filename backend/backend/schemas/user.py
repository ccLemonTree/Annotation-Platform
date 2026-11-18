"""
用户相关的数据模型
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional
from datetime import datetime


PLACEHOLDER_EMAIL_SUFFIX = "@placeholder.local"


class UserBase(BaseModel):
    """用户基础模型"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    full_name: Optional[str] = Field(None, max_length=100, description="全名")

    @field_validator("email", mode="before")
    @classmethod
    def empty_email_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class UserCreate(UserBase):
    """创建用户请求"""
    password: str = Field(..., min_length=6, max_length=72, description="密码，最多72个UTF-8字节")
    tenant_id: Optional[int] = Field(None, ge=1, description="兼容旧客户端；第一版使用默认租户")

    @field_validator("password")
    @classmethod
    def password_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过72个UTF-8字节")
        return value


class UserUpdate(BaseModel):
    """更新用户请求"""
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None

    @field_validator("email", mode="before")
    @classmethod
    def empty_email_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class UserResponse(BaseModel):
    """用户响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str]
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime]

    @field_validator("email", mode="before")
    @classmethod
    def hide_placeholder_email(cls, value):
        if isinstance(value, str) and value.endswith(PLACEHOLDER_EMAIL_SUFFIX):
            return None
        return value


class UserLogin(BaseModel):
    """用户登录请求"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class Token(BaseModel):
    """JWT令牌响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenData(BaseModel):
    """JWT令牌数据"""
    user_id: int
    tenant_id: int
    username: str
