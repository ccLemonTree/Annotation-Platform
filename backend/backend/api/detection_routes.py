"""
目标检测结果查询API
查询和统计来自SmartEye平台的检测结果
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from uuid import UUID

from database import get_db_session
from models.user import User
from api.deps import get_current_user, get_tenant_id


router = APIRouter()


# ==================== Pydantic模型 ====================

class DetectionQueryRequest(BaseModel):
    """检测结果查询请求"""
    platforms: Optional[List[str]] = Field(None, description="平台列表(可多选)")
    data_types: Optional[List[str]] = Field(None, description="数据类型列表")
    detection_targets: Optional[List[str]] = Field(None, description="检测目标列表(可多选)")
    equipment: Optional[str] = Field(None, description="设备ID")
    start_time: Optional[datetime] = Field(None, description="开始时间")
    end_time: Optional[datetime] = Field(None, description="结束时间")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class DetectionItem(BaseModel):
    """检测结果项"""
    platform: str
    data_type: str
    detection_target: str
    device_id: str
    detection_time: datetime
    total_count: int


class DetectionQueryResponse(BaseModel):
    """检测结果响应"""
    total: int
    page: int
    page_size: int
    items: List[DetectionItem]


class PlatformOption(BaseModel):
    """平台选项"""
    platform_id: str
    platform_name: str


class DetectionTargetOption(BaseModel):
    """检测目标选项"""
    target_id: str
    target_name: str


# ==================== API端点 ====================

@router.get("/platforms", response_model=List[PlatformOption])
async def get_platform_options(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """
    获取平台选项（用于下拉框）

    返回系统中配置的所有平台
    """
    # 从platforms表查询
    result = await db.execute(
        select(
            # 假设platforms表有platform_name和plat_id字段
        ).where(
            # enabled == True  # 只返回启用的平台
        )
    )

    # 暂时返回硬编码的平台列表
    # TODO: 从数据库platforms表读取
    return [
        PlatformOption(platform_id="xiaohongshu", platform_name="小红书"),
        PlatformOption(platform_id="douyin", platform_name="抖音"),
        PlatformOption(platform_id="weibo", platform_name="微博"),
        PlatformOption(platform_id="bilibili", platform_name="哔哩哔哩"),
    ]


@router.get("/detection-targets", response_model=List[DetectionTargetOption])
async def get_detection_target_options(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """
    获取检测目标选项（用于下拉框）

    返回系统中配置的所有检测目标类型
    """
    # 从alarm_types表查询
    result = await db.execute(
        select(
            # alarm_types表的alarm_name和alarm_code
        ).where(
            # enabled == True
        )
    )

    # 暂时返回硬编码的检测目标列表
    # TODO: 从数据库alarm_types表读取
    return [
        DetectionTargetOption(target_id="CAM-001", target_name="摄像头异常"),
        DetectionTargetOption(target_id="CAM-002", target_name="画面遮挡"),
        DetectionTargetOption(target_id="CAM-003", target_name="画面模糊"),
        DetectionTargetOption(target_id="FIRE-001", target_name="火灾检测"),
        DetectionTargetOption(target_id="SMOKE-001", target_name="烟雾检测"),
        DetectionTargetOption(target_id="PERSON-001", target_name="人员闯入"),
    ]


@router.post("/query", response_model=DetectionQueryResponse)
async def query_detections(
    query: DetectionQueryRequest,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """
    查询检测结果

    支持多条件组合查询：
    - 平台多选
    - 数据类型筛选
    - 检测目标多选
    - 设备ID
    - 时间范围

    **注意**: 当前返回模拟数据，需要实现与SmartEye平台的真实数据对接
    """
    # TODO: 实现真实的数据库查询逻辑
    # 1. 构建查询条件
    # 2. 从detection_results表或相关表查询
    # 3. 分页和排序

    # 暂时返回模拟数据用于前端调试
    mock_items = []

    # 根据查询条件生成模拟数据
    platforms = query.platforms or ["xiaohongshu", "douyin"]
    targets = query.detection_targets or ["CAM-001", "FIRE-001"]

    for i in range(min(query.page_size, 10)):
        mock_items.append(DetectionItem(
            platform=platforms[i % len(platforms)],
            data_type="image",
            detection_target=targets[i % len(targets)],
            device_id=query.equipment or f"CAM-{1000 + i}",
            detection_time=datetime.utcnow() - timedelta(hours=i),
            total_count=100 + i * 10
        ))

    return DetectionQueryResponse(
        total=50,  # 模拟总数
        page=query.page,
        page_size=query.page_size,
        items=mock_items
    )


@router.get("/statistics")
async def get_detection_statistics(
    start_time: Optional[datetime] = Query(None, description="开始时间"),
    end_time: Optional[datetime] = Query(None, description="结束时间"),
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """
    获取检测统计数据

    返回指定时间范围内的：
    - 总检测次数
    - 各平台检测次数分布
    - 各目标类型检测次数分布
    - 时间趋势图数据
    """
    # TODO: 实现真实的统计查询
    # 暂时返回模拟统计数据

    return {
        "total_detections": 1250,
        "by_platform": {
            "xiaohongshu": 450,
            "douyin": 380,
            "weibo": 280,
            "bilibili": 140
        },
        "by_target": {
            "CAM-001": 320,
            "FIRE-001": 280,
            "SMOKE-001": 250,
            "PERSON-001": 200,
            "其他": 200
        },
        "trend": [
            {"date": "2026-10-07", "count": 180},
            {"date": "2026-10-08", "count": 195},
            {"date": "2026-10-09", "count": 210},
            {"date": "2026-10-10", "count": 185},
            {"date": "2026-10-11", "count": 220},
            {"date": "2026-10-12", "count": 190},
            {"date": "2026-10-13", "count": 70}  # 今天进行中
        ]
    }
