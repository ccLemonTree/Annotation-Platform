"""
FastAPI主应用
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError
import logging

from database import engine
from models import Base
from api import api_router
from schemas.common import ResponseModel
from config.settings import settings
from middleware.performance import (
    PerformanceMonitorMiddleware,
    RequestLoggingMiddleware
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# 创建FastAPI应用
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="图像标注数据管理平台API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)


# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 性能监控中间件
app.add_middleware(PerformanceMonitorMiddleware)

# 请求日志中间件（仅在非生产环境启用）
if settings.APP_ENV == "development":
    app.add_middleware(RequestLoggingMiddleware)


# 全局异常处理
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    content = ResponseModel(success=False, code=exc.status_code, message=str(exc.detail), data=None).model_dump()
    content["detail"] = exc.detail
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """请求参数验证异常"""
    errors = [{key: error[key] for key in ("loc", "msg", "type") if key in error}
              for error in exc.errors()]
    logger.warning("Validation error: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ResponseModel(
            success=False,
            code=422,
            message="请求参数验证失败",
            data={"errors": errors}
        ).model_dump()
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    """数据库异常"""
    logger.error("Database error: %s", type(exc).__name__)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ResponseModel(
            success=False,
            code=500,
            message="数据库操作失败",
            data=None
        ).model_dump()
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """通用异常"""
    logger.error(f"Unexpected error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ResponseModel(
            success=False,
            code=500,
            message="服务器内部错误",
            data=None
        ).model_dump()
    )


# 启动事件
@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    logger.info("Starting application...")
    if not app.dependency_overrides:
        from integrations.smarteye.hooks import startup
        startup()
        from services.image_scenes import start_worker
        await start_worker()
        from services.image_uploads import start_worker as start_upload_worker
        await start_upload_worker()
        from services.automation import start_worker as start_automation_worker
        from services.image_semantics import start_worker as start_semantic_worker
        await start_automation_worker()
        await start_semantic_worker()
        from services.project_work import start_worker as start_project_work
        await start_project_work()
        from services.validation import start_worker as start_validation_worker
        await start_validation_worker()
        from services.training_runs import start_worker as start_training_worker
        await start_training_worker()
        from services.model_deployments import start_worker as start_deployment_worker
        await start_deployment_worker()
    # 注意：数据库表已经存在，不需要自动创建
    # 如果需要创建表，请运行 alembic upgrade head
    logger.info("Application started successfully")


# 关闭事件
@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    logger.info("Shutting down application...")
    from services.gpu_terminal import close_all as close_terminals
    await close_terminals()
    if not app.dependency_overrides:
        from integrations.smarteye.hooks import shutdown
        from services.image_scenes import stop_worker
        await stop_worker()
        from services.image_uploads import stop_worker as stop_upload_worker
        await stop_upload_worker()
        from services.automation import stop_worker as stop_automation_worker
        from services.image_semantics import stop_worker as stop_semantic_worker
        await stop_automation_worker()
        await stop_semantic_worker()
        from services.project_work import stop_worker as stop_project_work
        await stop_project_work()
        from services.validation import stop_worker as stop_validation_worker
        await stop_validation_worker()
        from services.training_runs import stop_worker as stop_training_worker
        await stop_training_worker()
        from services.model_deployments import stop_worker as stop_deployment_worker
        await stop_deployment_worker()
        shutdown()
    await engine.dispose()
    logger.info("Database connection closed")


# 健康检查
@app.get("/health", tags=["系统"])
async def health_check():
    """健康检查接口"""
    return ResponseModel(
        success=True,
        message="服务运行正常",
        data={"version": settings.VERSION}
    )


# 根路径
@app.get("/", tags=["系统"])
async def root():
    """根路径"""
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs"
    }


# 注册API路由
app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
