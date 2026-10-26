"""
训练管理 API路由
训练任务调度、模型管理
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from database import get_db_session
from models import TrainingTask, TrainingMetric, Model, Project, TrainingStatus


router = APIRouter()


# ==================== Pydantic模型 ====================

class CreateTrainingRequest(BaseModel):
    """创建训练任务请求"""
    project_id: UUID = Field(..., description="项目ID")
    task_name: str = Field(..., min_length=1, max_length=200, description="任务名称")
    framework: str = Field("pytorch", description="训练框架")
    model_type: str = Field(..., description="模型类型: yolov8/fasterrcnn/retinanet")
    config_yaml: str = Field(..., description="训练配置(YAML)")
    dataset_version: str = Field(..., description="数据集版本")
    gpu_count: int = Field(1, ge=1, le=8, description="GPU数量")
    gpu_type: str = Field("T4", description="GPU类型")


class TrainingTaskResponse(BaseModel):
    """训练任务响应"""
    id: int
    project_id: UUID
    task_name: str
    framework: str
    model_type: str
    status: str
    progress: int
    gpu_count: int
    gpu_type: str
    best_metric: Optional[float]
    final_loss: Optional[float]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class TrainingMetricResponse(BaseModel):
    """训练指标响应"""
    epoch: int
    step: int
    loss: float
    accuracy: Optional[float]
    learning_rate: Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True


class ModelResponse(BaseModel):
    """模型响应"""
    id: int
    project_id: UUID
    model_name: str
    model_version: str
    model_type: str
    framework: str
    model_size: int
    accuracy: Optional[float]
    f1_score: Optional[float]
    is_deployed: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ==================== API端点 ====================

@router.post("/tasks", response_model=TrainingTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_training_task(
    request: CreateTrainingRequest,
    db: AsyncSession = Depends(get_db_session)
):
    """
    创建训练任务

    **流程**：
    1. 生成YOLO数据集
    2. 创建K8s Job
    3. 异步监控训练进度
    """
    # 验证项目存在
    project_result = await db.execute(
        select(Project).where(Project.id == request.project_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {request.project_id} 不存在")

    # 创建训练任务
    task = TrainingTask(
        project_id=request.project_id,
        task_name=request.task_name,
        framework=request.framework,
        model_type=request.model_type,
        config_yaml=request.config_yaml,
        dataset_version=request.dataset_version,
        gpu_count=request.gpu_count,
        gpu_type=request.gpu_type,
        status=TrainingStatus.PENDING
    )

    db.add(task)
    await db.commit()
    await db.refresh(task)

    # TODO: 触发训练任务（K8s Job）
    # from services.training.k8s_manager import submit_training_job
    # submit_training_job.delay(task.id)

    return task


@router.get("/tasks", response_model=List[TrainingTaskResponse])
async def list_training_tasks(
    project_id: Optional[UUID] = None,
    status: Optional[TrainingStatus] = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取训练任务列表

    **过滤条件**：
    - project_id: 项目ID
    - status: 任务状态
    """
    query = select(TrainingTask)

    if project_id:
        query = query.where(TrainingTask.project_id == project_id)
    if status:
        query = query.where(TrainingTask.status == status)

    query = query.order_by(TrainingTask.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    tasks = result.scalars().all()

    return tasks


@router.get("/tasks/{task_id}", response_model=TrainingTaskResponse)
async def get_training_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """获取训练任务详情"""
    result = await db.execute(
        select(TrainingTask).where(TrainingTask.id == task_id)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail=f"训练任务 {task_id} 不存在")

    return task


@router.get("/tasks/{task_id}/metrics")
async def get_training_metrics(
    task_id: int,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取训练指标曲线

    **用途**：前端绘制Loss/Accuracy曲线
    """
    # 验证任务存在
    task_result = await db.execute(
        select(TrainingTask).where(TrainingTask.id == task_id)
    )
    task = task_result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=f"训练任务 {task_id} 不存在")

    # 查询指标
    metrics_result = await db.execute(
        select(TrainingMetric).where(
            TrainingMetric.task_id == task_id
        ).order_by(TrainingMetric.epoch.asc(), TrainingMetric.step.asc()).offset(skip).limit(limit)
    )
    metrics = metrics_result.scalars().all()

    return {
        "task_id": task_id,
        "task_name": task.task_name,
        "status": task.status,
        "total_metrics": len(metrics),
        "metrics": [
            {
                "epoch": m.epoch,
                "step": m.step,
                "loss": m.loss,
                "accuracy": m.accuracy,
                "learning_rate": m.learning_rate,
                "created_at": m.created_at
            }
            for m in metrics
        ]
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_training_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """
    取消训练任务

    **说明**：删除K8s Job，清理资源
    """
    # 验证任务存在
    task_result = await db.execute(
        select(TrainingTask).where(TrainingTask.id == task_id)
    )
    task = task_result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=f"训练任务 {task_id} 不存在")

    if task.status not in [TrainingStatus.PENDING, TrainingStatus.RUNNING]:
        raise HTTPException(status_code=400, detail=f"任务状态为 {task.status}，无法取消")

    # 更新状态
    task.status = TrainingStatus.CANCELLED
    task.completed_at = datetime.utcnow()

    # TODO: 删除K8s Job
    # from services.training.k8s_manager import delete_training_job
    # delete_training_job(task.k8s_job_name, task.k8s_namespace)

    await db.commit()

    return {
        "success": True,
        "message": f"训练任务 {task_id} 已取消"
    }


@router.get("/models", response_model=List[ModelResponse])
async def list_models(
    project_id: Optional[UUID] = None,
    is_deployed: Optional[bool] = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取模型列表

    **过滤条件**：
    - project_id: 项目ID
    - is_deployed: 是否已部署
    """
    query = select(Model)

    if project_id:
        query = query.where(Model.project_id == project_id)
    if is_deployed is not None:
        query = query.where(Model.is_deployed == is_deployed)

    query = query.order_by(Model.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    models = result.scalars().all()

    return models


@router.get("/models/{model_id}", response_model=ModelResponse)
async def get_model(
    model_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """获取模型详情"""
    result = await db.execute(
        select(Model).where(Model.id == model_id)
    )
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")

    return model


@router.post("/models/{model_id}/deploy")
async def deploy_model(
    model_id: int,
    deployment_config: Dict[str, Any] = {},
    db: AsyncSession = Depends(get_db_session)
):
    """
    部署模型

    **说明**：
    - 创建推理服务
    - 生成API端点
    """
    # 验证模型存在
    model_result = await db.execute(
        select(Model).where(Model.id == model_id)
    )
    model = model_result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")

    if model.is_deployed:
        raise HTTPException(status_code=400, detail="模型已部署")

    # TODO: 部署模型到推理服务
    # from services.deployment.inference_service import deploy_model
    # deployment_url = deploy_model(model.model_path, deployment_config)

    # 更新部署状态
    model.is_deployed = True
    model.deployment_url = f"http://inference-service:8080/models/{model_id}/predict"

    await db.commit()

    return {
        "success": True,
        "message": f"模型 {model_id} 部署成功",
        "deployment_url": model.deployment_url
    }


@router.delete("/models/{model_id}/undeploy")
async def undeploy_model(
    model_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """
    下线模型

    **说明**：删除推理服务
    """
    # 验证模型存在
    model_result = await db.execute(
        select(Model).where(Model.id == model_id)
    )
    model = model_result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")

    if not model.is_deployed:
        raise HTTPException(status_code=400, detail="模型未部署")

    # TODO: 删除推理服务
    # from services.deployment.inference_service import undeploy_model
    # undeploy_model(model.deployment_url)

    # 更新部署状态
    model.is_deployed = False
    model.deployment_url = None

    await db.commit()

    return {
        "success": True,
        "message": f"模型 {model_id} 已下线"
    }


@router.get("/export/{project_id}/yolo")
async def export_yolo_dataset(
    project_id: UUID,
    db: AsyncSession = Depends(get_db_session)
):
    """
    导出YOLO格式数据集

    **生成**：
    - data.yaml
    - images/
    - labels/
    - 自动划分训练集/验证集
    """
    # 验证项目存在
    project_result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")

    # TODO: 实现YOLO导出逻辑
    # from services.training.dataset_exporter import export_to_yolo
    # dataset_path = export_to_yolo(project_id)

    return {
        "success": True,
        "message": "数据集导出成功",
        "dataset_path": f"/datasets/project_{project_id}/yolo/",
        "format": "YOLOv8",
        "total_images": project.reviewed_count,
        "split": {
            "train": int(project.reviewed_count * 0.8),
            "val": int(project.reviewed_count * 0.2)
        }
    }
