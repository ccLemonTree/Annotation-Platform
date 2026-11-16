"""
数据库模型模块初始化
"""

from .base import Base
from .tenant import Tenant
from .user import User
from .project import Project, TaskType, ProjectStatus
from .project_member import ProjectMember
from .project_upload_key import ProjectUploadKey
from .asset import Asset
from .image import LibraryImage
from .image_ingest import ImageIngestSettings
from .image_collection import ImageCollection, ImageCollectionMember
from .image_upload import ImageUpload, ImageUploadItem
from .annotation import Annotation
from .annotation_event import AnnotationEvent, ExportSnapshot, ExportSnapshotJob
from .smarteye import SmarteyeTask, SmarteeyeDownload, TaskStatus
from .training import TrainingTask, TrainingMetric, Model, TrainingStatus
from .model_service import ModelService
from .automation import AutomationJob, AutomationItem
from .image_semantic import ImageSemanticJob, ImageSemanticItem, ImageSemanticVector
from .work_session import WorkActivityInterval
from .validation import ValidationSet, ValidationRun, ValidationItem
from .gpu_server import GpuServer
from .training_run import TrainingRun, TrainingGpuHold
from .model_deployment import ModelDeployment,ModelRelease,DeploymentTarget,DeploymentLease
from .model_repository import ModelRepositoryItem
from .project_work import ProjectWorkJob, ProjectWorkItem, ProjectWorkflowTemplate, MatchQuery
from .object_attribute_template import ObjectAttributeTemplate

__all__ = [
    'Base',
    'Tenant',
    'User',
    'Project',
    'ProjectMember',
    'TaskType',
    'ProjectStatus',
    'Asset',
    'Annotation',
    'SmarteyeTask',
    'SmarteeyeDownload',
    'TaskStatus',
    'TrainingTask',
    'TrainingMetric',
    'Model',
    'TrainingStatus',
]
