"""Strict connection input and credential-free server summaries."""
import ipaddress
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


Name = Annotated[str, Field(min_length=1, max_length=200)]
Host = Annotated[str, Field(min_length=1, max_length=45)]
Port = Annotated[int, Field(ge=1, le=65535)]
Username = Annotated[str, Field(min_length=1, max_length=64)]
Password = Annotated[SecretStr, Field(max_length=1024)]


class GpuServerInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)

    @field_validator('name', check_fields=False)
    @classmethod
    def valid_name(cls, value):
        if value is not None and not value.strip():
            raise ValueError('服务器名称不能为空')
        return value

    @field_validator('host', check_fields=False)
    @classmethod
    def valid_host(cls, value):
        if value is None:
            return value
        if '%' in value or any(c.isspace() for c in value) or '\0' in value:
            raise ValueError('请输入IPv4或IPv6字面量地址')
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise ValueError('请输入IPv4或IPv6字面量地址') from None

    @field_validator('username', check_fields=False)
    @classmethod
    def valid_username(cls, value):
        if value is not None and not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]{0,63}', value):
            raise ValueError('请输入有效Linux用户名')
        return value

    @field_validator('password', check_fields=False)
    @classmethod
    def valid_password(cls, value):
        if value is not None and '\0' in value.get_secret_value():
            raise ValueError('密码不能包含NUL字符')
        return value


class GpuServerTest(GpuServerInput):
    name: Name
    host: Host
    port: Port = 22
    username: Username = 'root'
    password: Annotated[Password, Field(min_length=1)] = Field(repr=False)


class GpuServerCreate(GpuServerTest):
    enabled: bool = True


class GpuServerUpdate(GpuServerInput):
    name: Name | None = None
    host: Host | None = None
    port: Port | None = None
    username: Username | None = None
    password: Password | None = Field(default=None, repr=False)
    enabled: bool | None = None
    clear_password: bool = False

    @model_validator(mode='after')
    def edit_fields(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {'password', 'clear_password'}):
            raise ValueError('服务器配置不能为null')
        if self.clear_password and self.password is not None:
            raise ValueError('重填密码与清除密码不能同时使用')
        return self


class GpuServerResponse(BaseModel):
    id: UUID | Literal['default']
    name: str
    host: str | None
    port: int | None
    username: str | None
    enabled: bool
    revision: int
    has_password: bool
    source: Literal['ssh_manager', 'password']
