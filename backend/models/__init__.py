"""
数据模型初始化
"""

from .base import Base, UUIDMixin, TimestampMixin
from .tenant import Tenant
from .user import User, UserRole
from .smarteye import Platform, AlarmType, SmartEyeTask, SmartEyeDownload
from .project import Project, ImportBatch, Asset, Annotation, Dataset, TaskType, ProjectStatus
from .database import engine, async_session_maker, get_db, init_db

__all__ = [
    # Base
    'Base',
    'UUIDMixin',
    'TimestampMixin',

    # Tenant & User
    'Tenant',
    'User',
    'UserRole',

    # SmartEye
    'Platform',
    'AlarmType',
    'SmartEyeTask',
    'SmartEyeDownload',

    # Project
    'Project',
    'ImportBatch',
    'Asset',
    'Annotation',
    'Dataset',
    'TaskType',
    'ProjectStatus',

    # Database
    'engine',
    'async_session_maker',
    'get_db',
    'init_db'
]
