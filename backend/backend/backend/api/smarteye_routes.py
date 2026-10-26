"""SmartEye integration API endpoints"""
from typing import List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.services.smarteye_import_service import SmartEyeImportService
from loguru import logger


router = APIRouter(
    prefix="/smarteye",
    tags=["SmartEye集成"]
)


class SmartEyeRecord(BaseModel):
    """SmartEye单条记录"""
    id: str = Field(..., description="SmartEye记录ID")
    image_path: str = Field(..., description="图片路径")
    alarm_type: str | None = Field(None, description="告警类型")
    device_id: str | None = Field(None, description="设备ID")
    device_name: str | None = Field(None, description="设备名称")
    timestamp: str | None = Field(None, description="时间戳")
    confidence: float | None = Field(None, description="置信度")
    bbox: Dict[str, Any] | None = Field(None, description="边界框")


class CreateAnnotationTaskRequest(BaseModel):
    """创建标注任务请求"""
    project_id: UUID = Field(..., description="目标项目ID")
    platform_id: UUID = Field(..., description="SmartEye平台配置ID")
    records: List[SmartEyeRecord] = Field(..., description="SmartEye数据记录")
    batch_name: str | None = Field(None, description="批次名称")

    class Config:
        json_schema_extra = {
            "example": {
                "project_id": "123e4567-e89b-12d3-a456-426614174000",
                "platform_id": "223e4567-e89b-12d3-a456-426614174001",
                "batch_name": "大门区域人员入侵_20241012",
                "records": [
                    {
                        "id": "smarteye_record_001",
                        "image_path": "/data/smarteye/images/2024/10/12/alarm_001.jpg",
                        "alarm_type": "人员入侵",
                        "device_id": "camera_001",
                        "device_name": "大门摄像头",
                        "timestamp": "2024-10-12 10:30:00",
                        "confidence": 0.95,
                        "bbox": {"x": 100, "y": 200, "width": 300, "height": 400}
                    }
                ]
            }
        }


class CreateAnnotationTaskResponse(BaseModel):
    """创建标注任务响应"""
    batch_id: UUID
    total_count: int
    success_count: int
    skipped_count: int
    failed_count: int
    errors: List[str]


@router.post(
    "/create-annotation-task",
    response_model=CreateAnnotationTaskResponse,
    summary="从SmartEye创建标注任务",
    description="""
    从SmartEye选中的数据创建标注任务（零拷贝导入）

    **工作流程**:
    1. 用户在SmartEye界面中筛选告警数据
    2. 点击"创建标注任务"按钮
    3. SmartEye前端通过postMessage将选中的数据发送到数据管理平台前端
    4. 数据管理平台前端调用此API
    5. 后端验证图片路径，计算哈希去重，创建Asset记录（零拷贝）

    **零拷贝机制**:
    - 不复制图片文件，直接引用SmartEye存储的路径
    - 通过content_hash去重，避免重复导入
    - 保存SmartEye原始数据到device_info字段
    """
)
async def create_annotation_task(
    request: CreateAnnotationTaskRequest,
    db: AsyncSession = Depends(get_db)
):
    """创建标注任务"""
    try:
        service = SmartEyeImportService(db)

        # 转换Pydantic模型为字典
        records_dict = [record.model_dump() for record in request.records]

        result = await service.import_from_smarteye(
            project_id=request.project_id,
            platform_id=request.platform_id,
            smarteye_records=records_dict,
            batch_name=request.batch_name
        )

        logger.info(
            f"SmartEye任务创建成功: batch_id={result['batch_id']}, "
            f"成功={result['success_count']}, 跳过={result['skipped_count']}, "
            f"失败={result['failed_count']}"
        )

        return result

    except ValueError as e:
        logger.error(f"参数错误: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"创建标注任务失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建任务失败: {str(e)}"
        )


class VerifyPathRequest(BaseModel):
    """验证路径请求"""
    platform_id: UUID
    image_paths: List[str] = Field(..., max_length=100, description="最多验证100个路径")


class VerifyPathResponse(BaseModel):
    """验证路径响应"""
    valid_paths: List[str]
    invalid_paths: List[str]


@router.post(
    "/verify-paths",
    response_model=VerifyPathResponse,
    summary="验证图片路径",
    description="批量验证SmartEye返回的图片路径是否有效"
)
async def verify_paths(
    request: VerifyPathRequest,
    db: AsyncSession = Depends(get_db)
):
    """验证图片路径是否可访问"""
    try:
        service = SmartEyeImportService(db)
        platform = await service.get_platform(request.platform_id)

        if not platform:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Platform {request.platform_id} not found"
            )

        valid_paths = []
        invalid_paths = []

        for path in request.image_paths:
            if service.verify_image_path(path, platform):
                valid_paths.append(path)
            else:
                invalid_paths.append(path)

        return {
            "valid_paths": valid_paths,
            "invalid_paths": invalid_paths
        }

    except Exception as e:
        logger.error(f"路径验证失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
