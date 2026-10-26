"""
API路由初始化
"""
from fastapi import APIRouter
from api import (
    auth_routes,
    user_routes,
    project_routes,
    asset_routes,
    image_routes,
    annotation_routes,
    batch_routes,
    export_routes,
    stats_routes
)


api_router = APIRouter()

from api.model_preview_routes import router as model_preview_router
api_router.include_router(model_preview_router,prefix='/model-previews',tags=['模型效果预览'])
from api.project_work_routes import router as project_work_router
api_router.include_router(project_work_router,prefix='/projects',tags=['项目标注处理'])
from api.gpu_terminal_routes import router as gpu_terminal_router
api_router.include_router(gpu_terminal_router, prefix='/gpu-terminal', tags=['SSH调试'])
from api.training_run_routes import router as training_run_router
api_router.include_router(training_run_router, prefix='/training', tags=['训练管理'])
from api.model_deployment_routes import router as model_deployment_router
api_router.include_router(model_deployment_router, prefix='/model-deployments', tags=['模型下发'])
from api.validation_routes import router as validation_router
api_router.include_router(validation_router, prefix='/validation', tags=['模型验证集'])
from api.image_semantic_routes import router as image_semantic_router
api_router.include_router(image_semantic_router, prefix='/image-semantics', tags=['图片描述与检索'])
from api.analytics_routes import router as analytics_router
api_router.include_router(analytics_router, prefix='/analytics', tags=['质量与效率分析'])
from api.automation_routes import router as automation_router
api_router.include_router(automation_router, prefix='/automation', tags=['预标注与AI审核'])
from api.gpu_resources_routes import router as gpu_resources_router
api_router.include_router(gpu_resources_router, prefix='/gpu-resources', tags=['GPU资源'])
from api.model_service_routes import router as model_service_router
api_router.include_router(model_service_router, prefix='/model-services', tags=['模型管理'])
from api.storage_cleanup_routes import router as storage_cleanup_router
api_router.include_router(storage_cleanup_router, prefix='/storage-maintenance', tags=['存储维护'])

# 认证相关路由
api_router.include_router(
    auth_routes.router,
    prefix="/auth",
    tags=["认证"]
)

# 用户管理路由
api_router.include_router(
    user_routes.router,
    prefix="/users",
    tags=["用户管理"]
)

# 项目管理路由
api_router.include_router(
    project_routes.router,
    prefix="/projects",
    tags=["项目管理"]
)

# 资产管理路由
api_router.include_router(
    asset_routes.router,
    prefix="/assets",
    tags=["资产管理"]
)

api_router.include_router(image_routes.router, prefix='/images', tags=['图片库'])
from api.image_upload_routes import router as image_upload_router
api_router.include_router(image_upload_router, prefix='/image-uploads', tags=['分片上传'])

# 标注管理路由
api_router.include_router(
    annotation_routes.router,
    prefix="/annotations",
    tags=["标注管理"]
)

# SmartEye集成路由
from integrations.smarteye.bridge import router as smarteye_router
api_router.include_router(
    smarteye_router,
    prefix="/smarteye",
    tags=["SmartEye集成"]
)

# 批量操作路由
api_router.include_router(
    batch_routes.router,
    prefix="/batch",
    tags=["批量操作"]
)

# 数据导出路由
api_router.include_router(
    export_routes.router,
    prefix="/export",
    tags=["数据导出"]
)

# 统计分析路由
api_router.include_router(
    stats_routes.router,
    prefix="/stats",
    tags=["统计分析"]
)
