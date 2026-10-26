"""
Schemas模块初始化
"""
from .common import ResponseModel, PaginationParams, PaginatedResponse
from .user import (
    UserCreate, UserUpdate, UserResponse, UserLogin,
    Token, TokenData
)
from .project import (
    ProjectCreate, ProjectUpdate, ProjectResponse, ProjectStats
)
from .asset import (
    AssetCreate, AssetResponse, AssetUploadResponse
)
from .annotation import (
    AnnotationCreate, AnnotationUpdate, AnnotationReview, AnnotationResponse
)

__all__ = [
    # Common
    'ResponseModel',
    'PaginationParams',
    'PaginatedResponse',
    # User
    'UserCreate',
    'UserUpdate',
    'UserResponse',
    'UserLogin',
    'Token',
    'TokenData',
    # Project
    'ProjectCreate',
    'ProjectUpdate',
    'ProjectResponse',
    'ProjectStats',
    # Asset
    'AssetCreate',
    'AssetResponse',
    'AssetUploadResponse',
    # Annotation
    'AnnotationCreate',
    'AnnotationUpdate',
    'AnnotationReview',
    'AnnotationResponse',
]
