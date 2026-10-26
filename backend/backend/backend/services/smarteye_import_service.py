"""SmartEye integration service - Zero-copy import from SmartEye data

This service handles importing alarm/ledger data from SmartEye into the
annotation platform without copying files.
"""
import os
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from backend.models.asset import Asset
from backend.models.batch import ImportBatch
from backend.models.platform import Platform


class SmartEyeImportService:
    """SmartEye数据导入服务（零拷贝架构）"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_platform(self, platform_id: UUID) -> Optional[Platform]:
        """获取Platform配置"""
        result = await self.db.execute(
            select(Platform).where(Platform.id == platform_id)
        )
        return result.scalar_one_or_none()

    def verify_image_path(self, image_path: str, platform: Platform) -> bool:
        """验证图片路径是否有效

        Args:
            image_path: SmartEye返回的图片路径
            platform: Platform配置（包含storage_config）

        Returns:
            bool: 路径是否有效
        """
        # 检查是否是绝对路径
        if not os.path.isabs(image_path):
            # 如果是相对路径，拼接base_path
            storage_config = platform.storage_config or {}
            base_path = storage_config.get('base_path', '/data/smarteye/images')
            image_path = os.path.join(base_path, image_path)

        # 检查文件是否存在
        return os.path.exists(image_path) and os.path.isfile(image_path)

    def calculate_file_hash(self, file_path: str) -> str:
        """计算文件SHA256哈希（用于去重）"""
        sha256_hash = hashlib.sha256()

        with open(file_path, 'rb') as f:
            # 分块读取，避免大文件内存溢出
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)

        return sha256_hash.hexdigest()

    async def import_from_smarteye(
        self,
        project_id: UUID,
        platform_id: UUID,
        smarteye_records: List[Dict[str, Any]],
        batch_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """从SmartEye数据创建标注任务（零拷贝导入）

        Args:
            project_id: 目标项目ID
            platform_id: SmartEye平台配置ID
            smarteye_records: SmartEye返回的数据记录列表
            batch_name: 批次名称（可选）

        SmartEye记录格式:
        {
            "id": "record_id_from_smarteye",
            "image_path": "/data/smarteye/images/2024/10/12/xxx.jpg",
            "alarm_type": "人员入侵",
            "device_id": "camera_001",
            "device_name": "大门摄像头",
            "timestamp": "2024-10-12 10:30:00",
            "confidence": 0.95,
            "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
            ... (其他SmartEye字段)
        }

        Returns:
            {
                "batch_id": UUID,
                "total_count": int,
                "success_count": int,
                "skipped_count": int,
                "failed_count": int,
                "errors": List[str]
            }
        """
        # 获取Platform配置
        platform = await self.get_platform(platform_id)
        if not platform:
            raise ValueError(f"Platform {platform_id} not found")

        # 创建导入批次
        batch = ImportBatch(
            project_id=project_id,
            platform_id=platform_id,
            batch_name=batch_name or f"SmartEye导入_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            source_type="smarteye",
            total_count=len(smarteye_records),
            success_count=0,
            skipped_count=0,
            failed_count=0
        )
        self.db.add(batch)
        await self.db.flush()  # 获取batch.id

        success_count = 0
        skipped_count = 0
        failed_count = 0
        errors = []

        for record in smarteye_records:
            try:
                image_path = record.get("image_path")
                if not image_path:
                    errors.append(f"记录 {record.get('id')} 缺少image_path字段")
                    failed_count += 1
                    continue

                # 验证路径
                if not self.verify_image_path(image_path, platform):
                    errors.append(f"图片路径不存在: {image_path}")
                    failed_count += 1
                    continue

                # 计算文件哈希（用于去重）
                content_hash = self.calculate_file_hash(image_path)

                # 检查是否已导入（同一项目中相同hash的图片）
                existing = await self.db.execute(
                    select(Asset).where(
                        and_(
                            Asset.project_id == project_id,
                            Asset.content_hash == content_hash
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    logger.debug(f"图片已存在，跳过: {image_path}")
                    skipped_count += 1
                    continue

                # 创建Asset记录（零拷贝：直接引用SmartEye的路径）
                asset = Asset(
                    project_id=project_id,
                    batch_id=batch.id,
                    platform_id=platform_id,

                    # 核心：零拷贝，直接引用SmartEye存储的路径
                    blob_path=image_path,
                    content_hash=content_hash,

                    # SmartEye源记录ID
                    source_record_id=record.get("id"),

                    # 设备和时间信息
                    device_id=record.get("device_id"),
                    capture_time=self._parse_timestamp(record.get("timestamp")),

                    # 扩展信息（保存SmartEye的原始数据）
                    device_info={
                        "platform": "SmartEye",
                        "device_name": record.get("device_name"),
                        "alarm_type": record.get("alarm_type"),
                        "confidence": record.get("confidence"),
                        "bbox": record.get("bbox"),
                        # 保存完整的原始记录
                        "raw_data": record
                    }
                )

                self.db.add(asset)
                success_count += 1

            except Exception as e:
                logger.error(f"导入记录失败: {e}", exc_info=True)
                errors.append(f"记录 {record.get('id')}: {str(e)}")
                failed_count += 1

        # 更新批次统计
        batch.success_count = success_count
        batch.skipped_count = skipped_count
        batch.failed_count = failed_count

        await self.db.commit()

        logger.info(
            f"SmartEye导入完成: 成功={success_count}, "
            f"跳过={skipped_count}, 失败={failed_count}"
        )

        return {
            "batch_id": batch.id,
            "total_count": len(smarteye_records),
            "success_count": success_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "errors": errors
        }

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[datetime]:
        """解析时间戳字符串"""
        if not timestamp_str:
            return None

        try:
            # 尝试多种时间格式
            for fmt in [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f"
            ]:
                try:
                    return datetime.strptime(timestamp_str, fmt)
                except ValueError:
                    continue

            logger.warning(f"无法解析时间格式: {timestamp_str}")
            return None

        except Exception as e:
            logger.error(f"时间解析错误: {e}")
            return None
