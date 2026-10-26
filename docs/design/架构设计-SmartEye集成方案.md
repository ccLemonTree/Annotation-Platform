# SmartEye集成方案设计

> 2026-10-13现行流程：SmartEye内置于主后端，上传／拉取先进入独立图片库，再由项目使用原图。同图可用于多个项目，标注和审核独立；可选拉取后自动建立项目引用。下方原设计保留作历史参考，旧“导入”和Vue实现建议以[图片库说明](../development/图片库与项目使用说明.md)及[当前TODO](../development/TODO.md)为准。

## 1. 需求分析

### 核心需求
1. **SmartEye独立功能保留**：某些场景只需拉取下载图片，不涉及标注训练
2. **数据复用**：标注训练项目可以直接使用SmartEye已下载的图片
3. **统一存储**：图片最终都要迁移到EOS，需要统一的存储抽象层
4. **避免重复下载**：SmartEye下载的图片可以直接导入标注项目

### 用户场景对比

| 场景 | 入口 | 核心流程 | 产物 |
|------|------|----------|------|
| **场景A：纯下载** | SmartEye页面 | 选平台→查询→下载ZIP | 本地图片包 |
| **场景B：标注训练** | 数据管理页面 | 选平台→导入→标注→训练 | 标注数据+模型 |
| **场景C：复用下载** | 数据管理页面 | 选SmartEye历史任务→导入 | 复用已下载图片 |

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                   数据管理平台（统一后端）                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              FastAPI 统一路由                              │   │
│  │  ┌────────────────┐  ┌────────────────┐                  │   │
│  │  │ SmartEye Routes│  │ Annotation     │                  │   │
│  │  │ /smarteye/*    │  │ Routes         │                  │   │
│  │  │                │  │ /projects/*    │                  │   │
│  │  └────────────────┘  └────────────────┘                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              统一服务层                                    │   │
│  │  ┌────────────────┐  ┌────────────────┐                  │   │
│  │  │ SmartEye       │  │ Annotation     │                  │   │
│  │  │ Service        │  │ Service        │                  │   │
│  │  │  ├─爬虫        │  │  ├─标注管理    │                  │   │
│  │  │  ├─认证        │  │  ├─审核流程    │                  │   │
│  │  │  └─任务管理    │  │  └─数据集生成  │                  │   │
│  │  └────────────────┘  └────────────────┘                  │   │
│  │            ▲                    ▲                          │   │
│  │            └────────┬───────────┘                          │   │
│  │                     ▼                                      │   │
│  │         ┌────────────────────┐                            │   │
│  │         │  统一存储抽象层     │ ⭐ 核心                    │   │
│  │         │  ├─ LocalStorage   │                            │   │
│  │         │  └─ EOSStorage     │                            │   │
│  │         └────────────────────┘                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              PostgreSQL 统一数据库                         │   │
│  │  smarteye_tasks  |  projects  |  assets  |  annotations  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                          ▲
                          │
┌─────────────────────────────────────────────────────────────────┐
│                   前端（Vue3 单页应用）                           │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐   │
│  │  SmartEye页面  │  │  数据导入页面  │  │  标注工作台     │   │
│  │  - 平台选择     │  │  - 从平台导入   │  │  - 矩形框标注   │   │
│  │  - 时间筛选     │  │  - 从SmartEye  │  │  - 质量审核     │   │
│  │  - 下载任务     │  │    导入         │  │  - 自动审核     │   │
│  │  - 历史记录     │  │  - 文件上传     │  │                 │   │
│  └────────────────┘  └────────────────┘  └────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

#### 原则1：前端分离，后端统一

```
✅ 推荐架构：
前端：
  - /smarteye          → SmartEye页面（独立菜单）
  - /projects          → 标注项目列表
  - /workspace/:id     → 标注工作台

后端：
  - FastAPI统一服务
  - 两个功能共享存储、数据库、认证

❌ 不推荐：
  - 两个独立的Flask + FastAPI后端
  - 两套存储系统
  - 两套用户认证
```

#### 原则2：数据复用，避免重复

```python
# SmartEye下载的图片保存到统一存储
# 数据库记录任务和文件路径
smarteye_tasks
  ├─ task_id: "task_001"
  ├─ platform: "平台A"
  ├─ downloaded_count: 100
  └─ storage_prefix: "smarteye/task_001/"

# 标注项目可以直接引用
assets
  ├─ id: "asset_001"
  ├─ source_type: "smarteye"  ⭐ 关键字段
  ├─ source_task_id: "task_001"
  └─ blob_path: "smarteye/task_001/abc.jpg"  # 引用同一文件
```

---

## 3. 数据库设计

### 3.1 核心表结构

```sql
-- =====================================================
-- SmartEye功能相关表
-- =====================================================

-- 平台配置表（迁移自SmartEye）
CREATE TABLE platforms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_name VARCHAR(100) UNIQUE NOT NULL,
    plat_id VARCHAR(100) UNIQUE NOT NULL,
    api_endpoint VARCHAR(500),
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 告警类型表（迁移自SmartEye）
CREATE TABLE alarm_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_id UUID REFERENCES platforms(id) ON DELETE CASCADE,
    alarm_name VARCHAR(100) NOT NULL,
    alarm_code VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(platform_id, alarm_code)
);

-- SmartEye下载任务表 ⭐ 新增
CREATE TABLE smarteye_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name VARCHAR(200),
    platform_id UUID REFERENCES platforms(id),
    
    -- 查询条件
    query_params JSONB NOT NULL,  -- {startTime, endTime, deviceId, alarmType}
    
    -- 任务状态
    status VARCHAR(50) DEFAULT 'pending',  -- pending/running/completed/failed
    progress INTEGER DEFAULT 0,  -- 0-100
    
    -- 统计信息
    total_count INTEGER DEFAULT 0,
    downloaded_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    
    -- 存储位置
    storage_prefix VARCHAR(500),  -- 如: "smarteye/2024-01-01/task_abc/"
    
    -- 元数据
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    
    -- 索引
    INDEX idx_smarteye_task_status (status),
    INDEX idx_smarteye_task_created (created_at DESC)
);

-- SmartEye下载记录表 ⭐ 新增
CREATE TABLE smarteye_downloads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES smarteye_tasks(id) ON DELETE CASCADE,
    
    -- 原始数据
    source_id VARCHAR(200) NOT NULL,  -- 平台原始记录ID
    platform VARCHAR(100),
    device_name VARCHAR(200),
    device_id VARCHAR(200),
    target_type VARCHAR(100),  -- 告警类型/目标类型
    timestamp TIMESTAMP,
    
    -- 存储信息
    blob_path VARCHAR(500),  -- 实际存储路径
    content_hash VARCHAR(64),  -- SHA256
    file_size BIGINT,
    original_url TEXT,
    
    -- 状态
    download_status VARCHAR(50) DEFAULT 'pending',
    error_message TEXT,
    
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- 索引
    INDEX idx_smarteye_download_task (task_id),
    INDEX idx_smarteye_download_hash (content_hash),
    UNIQUE(task_id, source_id)
);

-- =====================================================
-- 标注功能相关表（与SmartEye解耦）
-- =====================================================

-- 租户表
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 项目表
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    target_class VARCHAR(100),  -- 如: "红衣人"
    created_at TIMESTAMP DEFAULT NOW()
);

-- 资源表（核心：连接SmartEye和标注）⭐
CREATE TABLE assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    
    -- 来源标识 ⭐ 关键
    source_type VARCHAR(50) NOT NULL,  -- 'smarteye' / 'upload' / 'api'
    source_id VARCHAR(200),  -- SmartEye任务ID 或 上传批次ID
    source_record_id VARCHAR(200),  -- SmartEye下载记录ID
    
    -- 存储信息（复用SmartEye的存储）
    blob_path VARCHAR(500) NOT NULL,  -- 可能指向 smarteye_downloads.blob_path
    content_hash VARCHAR(64) NOT NULL,
    file_size BIGINT,
    
    -- 图片元数据
    width INTEGER,
    height INTEGER,
    format VARCHAR(20),
    
    -- 状态
    annotation_status VARCHAR(50) DEFAULT 'pending',
    review_status VARCHAR(50),
    
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- 索引
    INDEX idx_asset_project (project_id),
    INDEX idx_asset_source (source_type, source_id),
    INDEX idx_asset_hash (content_hash)
);

-- 标注表
CREATE TABLE annotations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID REFERENCES assets(id) ON DELETE CASCADE,
    bbox JSONB NOT NULL,  -- {x, y, width, height}
    label VARCHAR(100),
    confidence DECIMAL(3,2),
    annotator_type VARCHAR(50),  -- 'manual' / 'model' / 'auto'
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 3.2 关键关联关系

```sql
-- 查询：SmartEye任务下载的图片
SELECT sd.*, st.task_name 
FROM smarteye_downloads sd
JOIN smarteye_tasks st ON sd.task_id = st.id
WHERE st.id = 'task_001';

-- 查询：标注项目中来自SmartEye的图片
SELECT a.*, sd.device_name, sd.target_type
FROM assets a
JOIN smarteye_downloads sd ON a.source_record_id = sd.id::text
WHERE a.project_id = 'proj_001' 
  AND a.source_type = 'smarteye';

-- 查询：某个SmartEye下载记录被哪些项目使用
SELECT DISTINCT p.name, COUNT(a.id) as asset_count
FROM smarteye_downloads sd
LEFT JOIN assets a ON a.source_record_id = sd.id::text
LEFT JOIN projects p ON a.project_id = p.id
WHERE sd.task_id = 'task_001'
GROUP BY p.name;
```

---

## 4. 统一存储抽象层设计

### 4.1 接口定义

```python
# backend/services/storage/base.py

from abc import ABC, abstractmethod
from typing import BinaryIO, Optional, Dict
from pathlib import Path

class StorageBackend(ABC):
    """存储后端抽象接口"""
    
    @abstractmethod
    async def save_file(
        self, 
        file_data: BinaryIO, 
        path: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        保存文件
        
        Args:
            file_data: 文件数据流
            path: 相对路径，如 "smarteye/task_001/abc.jpg"
            metadata: 元数据 {content_hash, content_type, original_name}
        
        Returns:
            {
                "path": "存储路径",
                "size": 文件大小,
                "hash": "SHA256哈希"
            }
        """
        pass
    
    @abstractmethod
    async def get_file(self, path: str) -> BinaryIO:
        """获取文件流"""
        pass
    
    @abstractmethod
    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        pass
    
    @abstractmethod
    def get_public_url(self, path: str, expires: int = 3600) -> str:
        """
        生成访问URL
        
        Args:
            path: 文件路径
            expires: 有效期（秒），0表示永久
        
        Returns:
            签名URL（EOS）或代理URL（Local）
        """
        pass
    
    @abstractmethod
    async def copy_file(self, src_path: str, dst_path: str) -> bool:
        """
        复制文件（内部操作，无需下载上传）
        用于：SmartEye图片 → 标注项目 的零拷贝
        """
        pass
    
    @abstractmethod
    async def delete_file(self, path: str) -> bool:
        """删除文件"""
        pass
```

### 4.2 本地存储实现

```python
# backend/services/storage/local.py

import os
import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO, Dict, Optional

class LocalStorage(StorageBackend):
    """本地文件系统存储"""
    
    def __init__(self, base_path: str = "./data"):
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    async def save_file(
        self, 
        file_data: BinaryIO, 
        path: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        full_path = self.base_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入文件并计算哈希
        hasher = hashlib.sha256()
        file_size = 0
        
        with open(full_path, 'wb') as f:
            while chunk := file_data.read(8192):
                f.write(chunk)
                hasher.update(chunk)
                file_size += len(chunk)
        
        content_hash = hasher.hexdigest()
        
        # 校验元数据中的哈希（如果提供）
        if metadata and metadata.get('content_hash'):
            assert content_hash == metadata['content_hash'], "哈希校验失败"
        
        return {
            "path": path,
            "size": file_size,
            "hash": content_hash
        }
    
    async def get_file(self, path: str) -> BinaryIO:
        full_path = self.base_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        return open(full_path, 'rb')
    
    async def file_exists(self, path: str) -> bool:
        return (self.base_path / path).exists()
    
    def get_public_url(self, path: str, expires: int = 3600) -> str:
        # 本地存储通过API代理访问
        return f"/api/storage/files/{path}"
    
    async def copy_file(self, src_path: str, dst_path: str) -> bool:
        """零拷贝：硬链接或符号链接"""
        src_full = self.base_path / src_path
        dst_full = self.base_path / dst_path
        
        if not src_full.exists():
            return False
        
        dst_full.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # 尝试硬链接（节省空间）
            os.link(src_full, dst_full)
        except OSError:
            # 降级到符号链接
            os.symlink(src_full, dst_full)
        
        return True
    
    async def delete_file(self, path: str) -> bool:
        full_path = self.base_path / path
        if full_path.exists():
            full_path.unlink()
            return True
        return False
```

### 4.3 EOS存储实现

```python
# backend/services/storage/eos.py

import boto3
from botocore.client import Config

class EOSStorage(StorageBackend):
    """EOS对象存储（S3兼容）"""
    
    def __init__(
        self, 
        endpoint: str, 
        bucket: str, 
        access_key: str, 
        secret_key: str,
        region: str = "us-east-1"
    ):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version='s3v4')
        )
        self.bucket = bucket
    
    async def save_file(
        self, 
        file_data: BinaryIO, 
        path: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        # 上传到EOS
        extra_args = {}
        if metadata:
            extra_args['Metadata'] = {
                'sha256': metadata.get('content_hash', ''),
                'original-name': metadata.get('original_name', '')
            }
            if metadata.get('content_type'):
                extra_args['ContentType'] = metadata['content_type']
        
        self.s3_client.upload_fileobj(
            file_data,
            self.bucket,
            path,
            ExtraArgs=extra_args
        )
        
        # 获取对象元数据
        head = self.s3_client.head_object(Bucket=self.bucket, Key=path)
        
        return {
            "path": path,
            "size": head['ContentLength'],
            "hash": head['Metadata'].get('sha256', '')
        }
    
    async def get_file(self, path: str) -> BinaryIO:
        response = self.s3_client.get_object(Bucket=self.bucket, Key=path)
        return response['Body']
    
    async def file_exists(self, path: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=path)
            return True
        except:
            return False
    
    def get_public_url(self, path: str, expires: int = 3600) -> str:
        if expires == 0:
            # 永久URL（需要bucket开启公开读）
            return f"{self.s3_client.meta.endpoint_url}/{self.bucket}/{path}"
        else:
            # 预签名URL
            return self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': path},
                ExpiresIn=expires
            )
    
    async def copy_file(self, src_path: str, dst_path: str) -> bool:
        """EOS内部复制（服务端操作）"""
        try:
            self.s3_client.copy_object(
                CopySource={'Bucket': self.bucket, 'Key': src_path},
                Bucket=self.bucket,
                Key=dst_path
            )
            return True
        except:
            return False
    
    async def delete_file(self, path: str) -> bool:
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=path)
            return True
        except:
            return False
```

### 4.4 工厂方法

```python
# backend/services/storage/__init__.py

from .base import StorageBackend
from .local import LocalStorage
from .eos import EOSStorage
from ..config import settings

_storage_instance: Optional[StorageBackend] = None

def get_storage() -> StorageBackend:
    """单例工厂：根据配置返回存储实例"""
    global _storage_instance
    
    if _storage_instance is None:
        storage_type = settings.STORAGE_TYPE
        
        if storage_type == "local":
            _storage_instance = LocalStorage(settings.STORAGE_LOCAL_PATH)
        elif storage_type == "eos":
            _storage_instance = EOSStorage(
                endpoint=settings.EOS_ENDPOINT,
                bucket=settings.EOS_BUCKET,
                access_key=settings.EOS_ACCESS_KEY,
                secret_key=settings.EOS_SECRET_KEY
            )
        else:
            raise ValueError(f"不支持的存储类型: {storage_type}")
    
    return _storage_instance
```

---

## 5. SmartEye服务实现

### 5.1 下载任务服务

```python
# backend/services/smarteye/download_service.py

import asyncio
import aiohttp
from typing import Dict, List
from uuid import UUID, uuid4
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from ..storage import get_storage
from ...models.smarteye import SmartEyeTask, SmartEyeDownload
from .crawlers.alarm_crawler import AlarmCrawler

class SmartEyeDownloadService:
    """SmartEye下载任务服务"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = get_storage()
        self.alarm_crawler = AlarmCrawler()
    
    async def create_task(
        self,
        task_name: str,
        platform_id: UUID,
        query_params: Dict
    ) -> SmartEyeTask:
        """创建下载任务"""
        
        # 1. 生成存储前缀
        date_str = datetime.now().strftime("%Y-%m-%d")
        task_id_short = str(uuid4())[:8]
        storage_prefix = f"smarteye/{date_str}/task_{task_id_short}/"
        
        # 2. 创建任务记录
        task = SmartEyeTask(
            task_name=task_name,
            platform_id=platform_id,
            query_params=query_params,
            status="pending",
            storage_prefix=storage_prefix
        )
        
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        
        return task
    
    async def execute_task(self, task_id: UUID):
        """执行下载任务（后台任务）"""
        
        # 1. 更新任务状态
        task = await self.db.get(SmartEyeTask, task_id)
        task.status = "running"
        task.started_at = datetime.now()
        await self.db.commit()
        
        try:
            # 2. 调用爬虫获取记录列表
            records = await self.alarm_crawler.fetch_records(
                platform_id=task.platform_id,
                query_params=task.query_params
            )
            
            task.total_count = len(records)
            await self.db.commit()
            
            # 3. 并发下载图片
            async with aiohttp.ClientSession() as session:
                download_tasks = [
                    self._download_single_image(
                        session, 
                        task, 
                        record, 
                        idx
                    )
                    for idx, record in enumerate(records)
                ]
                
                results = await asyncio.gather(*download_tasks, return_exceptions=True)
            
            # 4. 统计结果
            success_count = sum(1 for r in results if r is True)
            failed_count = len(results) - success_count
            
            task.downloaded_count = success_count
            task.failed_count = failed_count
            task.status = "completed"
            task.completed_at = datetime.now()
            
        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
        
        finally:
            await self.db.commit()
    
    async def _download_single_image(
        self,
        session: aiohttp.ClientSession,
        task: SmartEyeTask,
        record: Dict,
        index: int
    ) -> bool:
        """下载单张图片"""
        
        try:
            # 1. 下载图片
            async with session.get(record['picUrl'], timeout=30) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                
                image_data = await resp.read()
            
            # 2. 计算哈希
            import hashlib
            content_hash = hashlib.sha256(image_data).hexdigest()
            
            # 3. 检查是否已存在（去重）
            existing = await self.db.execute(
                select(SmartEyeDownload).where(
                    SmartEyeDownload.content_hash == content_hash
                )
            )
            if existing.scalar_one_or_none():
                # 已存在，跳过但记录关联
                pass
            
            # 4. 保存到存储
            file_name = f"{index:06d}_{content_hash[:16]}.jpg"
            blob_path = f"{task.storage_prefix}{file_name}"
            
            from io import BytesIO
            await self.storage.save_file(
                BytesIO(image_data),
                blob_path,
                metadata={'content_hash': content_hash}
            )
            
            # 5. 记录到数据库
            download_record = SmartEyeDownload(
                task_id=task.id,
                source_id=record['id'],
                platform=record['platform'],
                device_name=record['device'],
                device_id=record['deviceId'],
                target_type=record['targetType'],
                timestamp=datetime.fromisoformat(record['timestamp']),
                blob_path=blob_path,
                content_hash=content_hash,
                file_size=len(image_data),
                original_url=record['picUrl'],
                download_status="completed"
            )
            
            self.db.add(download_record)
            await self.db.commit()
            
            # 6. 更新进度
            task.downloaded_count += 1
            task.progress = int((task.downloaded_count / task.total_count) * 100)
            await self.db.commit()
            
            return True
            
        except Exception as e:
            # 记录失败
            download_record = SmartEyeDownload(
                task_id=task.id,
                source_id=record.get('id', ''),
                download_status="failed",
                error_message=str(e)
            )
            self.db.add(download_record)
            await self.db.commit()
            
            return False
```

---

## 6. 数据导入服务（复用SmartEye）

### 6.1 从SmartEye任务导入

```python
# backend/services/annotation/import_service.py

from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..storage import get_storage
from ...models.smarteye import SmartEyeTask, SmartEyeDownload
from ...models.annotation import Project, Asset

class ImportService:
    """数据导入服务"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = get_storage()
    
    async def import_from_smarteye(
        self,
        project_id: UUID,
        smarteye_task_id: UUID,
        filters: Optional[Dict] = None
    ) -> Dict:
        """
        从SmartEye任务导入图片到标注项目
        
        Args:
            project_id: 目标标注项目ID
            smarteye_task_id: SmartEye任务ID
            filters: 可选过滤条件 {target_type, device_id, date_range}
        
        Returns:
            {
                "imported_count": 100,
                "skipped_count": 5,  # 已存在
                "asset_ids": [...]
            }
        """
        
        # 1. 查询SmartEye下载记录
        query = select(SmartEyeDownload).where(
            SmartEyeDownload.task_id == smarteye_task_id,
            SmartEyeDownload.download_status == "completed"
        )
        
        # 应用过滤器
        if filters:
            if filters.get('target_type'):
                query = query.where(
                    SmartEyeDownload.target_type == filters['target_type']
                )
            if filters.get('device_id'):
                query = query.where(
                    SmartEyeDownload.device_id == filters['device_id']
                )
        
        result = await self.db.execute(query)
        downloads = result.scalars().all()
        
        # 2. 创建Asset记录（引用SmartEye的文件）
        imported_count = 0
        skipped_count = 0
        asset_ids = []
        
        for download in downloads:
            # 检查是否已导入
            existing = await self.db.execute(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.source_type == "smarteye",
                    Asset.source_record_id == str(download.id)
                )
            )
            if existing.scalar_one_or_none():
                skipped_count += 1
                continue
            
            # ⭐ 关键：直接引用SmartEye的存储路径，无需复制文件
            asset = Asset(
                project_id=project_id,
                source_type="smarteye",
                source_id=str(smarteye_task_id),
                source_record_id=str(download.id),
                blob_path=download.blob_path,  # 复用路径
                content_hash=download.content_hash,
                file_size=download.file_size,
                annotation_status="pending"
            )
            
            self.db.add(asset)
            imported_count += 1
            asset_ids.append(asset.id)
        
        await self.db.commit()
        
        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "asset_ids": asset_ids
        }
```

### 6.2 关键优势：零拷贝导入

```python
# 传统方案：需要复制文件
# 1. SmartEye下载 → /downloads/smarteye/abc.jpg
# 2. 导入项目时复制 → /data/projects/proj1/abc.jpg
# 问题：磁盘空间翻倍、耗时长

# 新方案：引用同一文件
# 1. SmartEye下载 → blob_path: "smarteye/task_001/abc.jpg"
# 2. Asset记录 → blob_path: "smarteye/task_001/abc.jpg" (同一路径)
# 3. 统一存储层自动处理（本地是硬链接，EOS是引用）
# 优势：零拷贝、瞬间完成、节省空间
```

---

## 7. API设计

### 7.1 SmartEye相关API

```python
# backend/api/smarteye_routes.py

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from uuid import UUID

from ..services.smarteye.download_service import SmartEyeDownloadService
from ..dependencies import get_db

router = APIRouter(prefix="/api/smarteye", tags=["SmartEye"])

@router.post("/tasks")
async def create_download_task(
    task_name: str,
    platform_id: UUID,
    query_params: Dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """创建SmartEye下载任务"""
    service = SmartEyeDownloadService(db)
    
    # 1. 创建任务
    task = await service.create_task(task_name, platform_id, query_params)
    
    # 2. 后台执行
    background_tasks.add_task(service.execute_task, task.id)
    
    return {
        "task_id": task.id,
        "status": "pending",
        "message": "任务已创建，正在后台执行"
    }

@router.get("/tasks/{task_id}")
async def get_task_progress(
    task_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """获取任务进度"""
    task = await db.get(SmartEyeTask, task_id)
    
    return {
        "task_id": task.id,
        "task_name": task.task_name,
        "status": task.status,
        "progress": task.progress,
        "total_count": task.total_count,
        "downloaded_count": task.downloaded_count,
        "failed_count": task.failed_count,
        "created_at": task.created_at,
        "completed_at": task.completed_at
    }

@router.get("/tasks")
async def list_tasks(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """获取任务列表"""
    query = select(SmartEyeTask)
    
    if status:
        query = query.where(SmartEyeTask.status == status)
    
    query = query.order_by(SmartEyeTask.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return {
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": t.id,
                "task_name": t.task_name,
                "status": t.status,
                "progress": t.progress,
                "total_count": t.total_count,
                "downloaded_count": t.downloaded_count,
                "created_at": t.created_at
            }
            for t in tasks
        ]
    }

@router.get("/tasks/{task_id}/downloads")
async def list_task_downloads(
    task_id: UUID,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """获取任务下载的图片列表"""
    query = select(SmartEyeDownload).where(
        SmartEyeDownload.task_id == task_id,
        SmartEyeDownload.download_status == "completed"
    ).offset((page - 1) * page_size).limit(page_size)
    
    result = await db.execute(query)
    downloads = result.scalars().all()
    
    storage = get_storage()
    
    return {
        "page": page,
        "items": [
            {
                "id": d.id,
                "device_name": d.device_name,
                "target_type": d.target_type,
                "timestamp": d.timestamp,
                "file_size": d.file_size,
                "thumbnail_url": storage.get_public_url(d.blob_path)
            }
            for d in downloads
        ]
    }

@router.get("/tasks/{task_id}/download-zip")
async def download_task_zip(
    task_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """下载任务所有图片的ZIP包（保持原SmartEye功能）"""
    # TODO: 实现ZIP打包逻辑
    pass
```

### 7.2 数据导入API

```python
# backend/api/import_routes.py

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from ..services.annotation.import_service import ImportService

router = APIRouter(prefix="/api/projects/{project_id}/import", tags=["Import"])

@router.post("/from-smarteye")
async def import_from_smarteye(
    project_id: UUID,
    smarteye_task_id: UUID,
    filters: Optional[Dict] = None,
    db: AsyncSession = Depends(get_db)
):
    """从SmartEye任务导入图片"""
    service = ImportService(db)
    
    result = await service.import_from_smarteye(
        project_id=project_id,
        smarteye_task_id=smarteye_task_id,
        filters=filters
    )
    
    return {
        "success": True,
        "imported_count": result['imported_count'],
        "skipped_count": result['skipped_count'],
        "message": f"成功导入 {result['imported_count']} 张图片"
    }

@router.get("/smarteye-tasks")
async def list_available_smarteye_tasks(
    project_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """获取可导入的SmartEye任务列表"""
    query = select(SmartEyeTask).where(
        SmartEyeTask.status == "completed",
        SmartEyeTask.downloaded_count > 0
    ).order_by(SmartEyeTask.completed_at.desc())
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return {
        "items": [
            {
                "id": t.id,
                "task_name": t.task_name,
                "platform": t.platform.platform_name,
                "downloaded_count": t.downloaded_count,
                "query_params": t.query_params,
                "completed_at": t.completed_at
            }
            for t in tasks
        ]
    }
```

---

## 8. 前端页面设计

### 8.1 菜单结构

```
数据管理平台
├─ SmartEye（独立菜单）
│  ├─ 新建任务
│  ├─ 任务列表
│  └─ 历史记录
│
├─ 标注项目
│  ├─ 项目列表
│  ├─ 创建项目
│  └─ 数据导入 ⭐
│     ├─ 从平台导入（直接调用爬虫）
│     ├─ 从SmartEye导入（复用已下载）
│     └─ 文件上传
│
└─ 标注工作台
   ├─ 图片浏览
   ├─ 矩形框标注
   └─ 审核流程
```

### 8.2 SmartEye页面（保持原功能）

```vue
<!-- frontend/src/views/SmartEye/TaskList.vue -->

<template>
  <el-card>
    <el-button type="primary" @click="showCreateDialog = true">
      新建下载任务
    </el-button>
    
    <el-table :data="tasks" style="margin-top: 20px;">
      <el-table-column prop="task_name" label="任务名称" />
      <el-table-column prop="platform" label="平台" />
      <el-table-column label="状态">
        <template #default="{ row }">
          <el-tag :type="getStatusType(row.status)">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度">
        <template #default="{ row }">
          <el-progress 
            :percentage="row.progress" 
            :status="row.status === 'completed' ? 'success' : null"
          />
          <span>{{ row.downloaded_count }} / {{ row.total_count }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button 
            size="small" 
            @click="viewDownloads(row.id)"
          >
            查看图片
          </el-button>
          <el-button 
            size="small" 
            type="success"
            :disabled="row.status !== 'completed'"
            @click="downloadZip(row.id)"
          >
            下载ZIP
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { listTasks, downloadTaskZip } from '@/api/smarteye';

const tasks = ref([]);
const showCreateDialog = ref(false);

const loadTasks = async () => {
  const res = await listTasks();
  tasks.value = res.items;
};

const downloadZip = async (taskId: string) => {
  const url = await downloadTaskZip(taskId);
  window.open(url);
  ElMessage.success('ZIP包准备中，请稍候...');
};

onMounted(loadTasks);
</script>
```

### 8.3 数据导入页面（新增）

```vue
<!-- frontend/src/views/Projects/Import.vue -->

<template>
  <el-card>
    <el-tabs v-model="activeTab">
      <!-- Tab 1: 从平台直接导入 -->
      <el-tab-pane label="从平台导入" name="platform">
        <el-form :model="platformForm">
          <el-form-item label="平台">
            <el-select v-model="platformForm.platform_id">
              <el-option 
                v-for="p in platforms" 
                :key="p.id" 
                :label="p.name" 
                :value="p.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="时间范围">
            <el-date-picker 
              v-model="platformForm.dateRange"
              type="datetimerange"
            />
          </el-form-item>
          <el-form-item label="设备ID（可选）">
            <el-input v-model="platformForm.device_id" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="importFromPlatform">
              开始导入
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
      
      <!-- Tab 2: 从SmartEye导入 ⭐ 核心功能 -->
      <el-tab-pane label="从SmartEye导入" name="smarteye">
        <p class="tip">
          💡 复用SmartEye已下载的图片，无需重新下载，瞬间完成
        </p>
        
        <el-table 
          :data="smarteyeTasks" 
          @row-click="handleSelectTask"
          highlight-current-row
        >
          <el-table-column type="selection" width="55" />
          <el-table-column prop="task_name" label="任务名称" />
          <el-table-column prop="platform" label="平台" />
          <el-table-column prop="downloaded_count" label="图片数量" />
          <el-table-column prop="completed_at" label="完成时间" />
          <el-table-column label="查询条件">
            <template #default="{ row }">
              <el-tag size="small">
                {{ row.query_params.startTime }} ~ {{ row.query_params.endTime }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        
        <!-- 筛选条件 -->
        <el-card v-if="selectedTask" style="margin-top: 20px;">
          <h4>可选筛选条件</h4>
          <el-form :model="smarteyeFilters">
            <el-form-item label="目标类型">
              <el-select v-model="smarteyeFilters.target_type">
                <el-option label="全部" value="" />
                <el-option label="红衣人" value="红衣人" />
                <el-option label="黄衣人" value="黄衣人" />
              </el-select>
            </el-form-item>
            <el-form-item label="设备">
              <el-select v-model="smarteyeFilters.device_id">
                <el-option label="全部设备" value="" />
              </el-select>
            </el-form-item>
          </el-form>
          
          <el-button type="primary" @click="importFromSmartEye">
            导入到当前项目
          </el-button>
        </el-card>
      </el-tab-pane>
      
      <!-- Tab 3: 文件上传 -->
      <el-tab-pane label="文件上传" name="upload">
        <el-upload 
          drag 
          multiple
          :action="`/api/projects/${projectId}/import/upload`"
          :on-success="handleUploadSuccess"
        >
          <el-icon><upload-filled /></el-icon>
          <div>拖拽文件到此处或点击上传</div>
        </el-upload>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import { 
  listAvailableSmartEyeTasks, 
  importFromSmartEye 
} from '@/api/import';

const route = useRoute();
const projectId = route.params.id;

const activeTab = ref('smarteye');
const smarteyeTasks = ref([]);
const selectedTask = ref(null);
const smarteyeFilters = ref({
  target_type: '',
  device_id: ''
});

const loadSmartEyeTasks = async () => {
  const res = await listAvailableSmartEyeTasks(projectId);
  smarteyeTasks.value = res.items;
};

const handleSelectTask = (row) => {
  selectedTask.value = row;
};

const importFromSmartEye = async () => {
  if (!selectedTask.value) {
    ElMessage.warning('请选择一个SmartEye任务');
    return;
  }
  
  const res = await importFromSmartEye(
    projectId,
    selectedTask.value.id,
    smarteyeFilters.value
  );
  
  ElMessage.success(res.message);
  // 跳转到图片列表
  router.push(`/projects/${projectId}/assets`);
};

onMounted(loadSmartEyeTasks);
</script>

<style scoped>
.tip {
  padding: 10px;
  background: #f0f9ff;
  border-left: 4px solid #3b82f6;
  margin-bottom: 20px;
}
</style>
```

---

## 9. 配置文件示例

```bash
# .env（统一配置）

# ===== 应用配置 =====
APP_NAME=数据管理平台
APP_ENV=development
DEBUG=true

# ===== 存储配置 =====
STORAGE_TYPE=local  # local / eos
STORAGE_LOCAL_PATH=./data

# EOS配置（生产环境）
# STORAGE_TYPE=eos
# EOS_ENDPOINT=http://eos.example.com
# EOS_BUCKET=data-platform
# EOS_ACCESS_KEY=your_access_key
# EOS_SECRET_KEY=your_secret_key
# EOS_REGION=us-east-1

# ===== 数据库配置 =====
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=data_platform

# ===== SmartEye配置 =====
SMARTEYE_API_BASE_URL=http://子平台地址
SMARTEYE_USERNAME=admin
SMARTEYE_PASSWORD=password
SMARTEYE_MAX_WORKERS=10

# ===== 模型服务配置 =====
QWEN_API_URL=http://localhost:8000/v1
QWEN_API_KEY=sk-xxx

EMBEDDING_API_URL=http://localhost:8001/v1
RERANKER_API_URL=http://localhost:8002/v1

# ===== Redis配置（可选）=====
# REDIS_HOST=localhost
# REDIS_PORT=6379

# ===== Kubernetes配置（训练）=====
K8S_KUBECONFIG=~/.kube/config
K8S_NAMESPACE=training
```

---

## 10. 迁移路径（分阶段实施）

### Phase 0：环境准备（1天）

- [x] 设计架构文档
- [ ] 创建项目目录结构
- [ ] 初始化Git仓库
- [ ] 配置开发环境

### Phase 1：存储层 + 数据库（2-3天）

```bash
# 任务清单
□ 实现StorageBackend接口
□ 实现LocalStorage
□ 实现EOSStorage（接口定义）
□ 编写存储层单元测试
□ 创建数据库表结构
□ 编写Alembic迁移脚本
```

### Phase 2：SmartEye核心迁移（3-5天）

```bash
# 优先级顺序
□ 迁移平台管理（platform_enum.py → platform_manager.py）
□ 迁移登录认证（login_utils.py → auth.py）
□ 迁移告警爬虫（alarm_data_crawler.py → alarm_crawler.py）
□ 实现SmartEyeDownloadService
□ 实现SmartEye API路由
□ 测试下载任务完整流程
```

### Phase 3：前端SmartEye页面（2-3天）

```bash
□ 创建SmartEye菜单和路由
□ 实现任务列表页面
□ 实现新建任务表单
□ 实现进度查看
□ 实现图片预览
□ 实现ZIP下载
```

### Phase 4：数据导入功能（2-3天）

```bash
□ 实现ImportService
□ 实现从SmartEye导入API
□ 实现前端导入页面
□ 测试零拷贝导入
□ 测试筛选功能
```

### Phase 5：标注功能（5-7天）

```bash
□ 实现资源管理API
□ 实现标注API
□ 实现审核API
□ 实现标注工作台前端
□ 集成模型服务（Qwen）
```

### Phase 6：训练集成（3-5天）

```bash
□ 实现数据集导出
□ 实现训练任务调度
□ 对接K8s
□ 实现模型管理
```

### Phase 7：部署上线（2-3天）

```bash
□ 编写Dockerfile
□ 配置Nginx
□ 测试EOS存储切换
□ 性能测试
□ 用户验收测试
```

---

## 11. 关键技术决策总结

| 决策点 | 选择 | 理由 |
|--------|------|------|
| **架构模式** | 前端分离 + 后端统一 | SmartEye功能独立但共享后端服务 |
| **存储抽象** | 接口 + 工厂模式 | 统一接口，轻松切换Local/EOS |
| **数据复用** | 引用同一文件路径 | 零拷贝，节省空间和时间 |
| **数据库** | PostgreSQL单库 | SmartEye和标注数据统一管理 |
| **后端框架** | FastAPI | 异步高性能，类型安全 |
| **前端框架** | Vue3 + Element Plus | 成熟稳定，组件丰富 |
| **迁移策略** | 渐进式迁移 | 保持SmartEye独立运行，逐步迁移模块 |

---

## 12. 预期效果

### 对用户

1. **SmartEye用户**：
   - 保留完整功能（平台查询、批量下载、ZIP导出）
   - 操作习惯不变
   - 性能无损失

2. **标注项目用户**：
   - 可以直接复用SmartEye下载的图片
   - 导入速度极快（秒级完成）
   - 节省下载时间和带宽

3. **系统管理员**：
   - 统一的存储管理
   - 统一的用户权限
   - 统一的监控日志
   - 迁移EOS时只需改配置

### 技术指标

```
导入速度：
  - 传统方案：100张图片 ≈ 5分钟（下载+复制）
  - 新方案：100张图片 ≈ 5秒（数据库记录）
  - 提升：60倍

存储效率：
  - SmartEye下载：1GB
  - 标注项目导入：0GB（引用）
  - 节省：100%

迁移EOS：
  - 代码改动：0行
  - 配置改动：5行
  - 数据迁移：rsync一次
```

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| SmartEye迁移工作量大 | 延期 | 保持原Flask项目临时运行，逐步迁移核心模块 |
| 文件引用导致误删 | 数据丢失 | 实现引用计数，删除前检查 |
| EOS迁移失败 | 无法上线 | 先在测试环境验证，提供回滚方案 |
| 性能瓶颈 | 用户体验差 | 提前进行压力测试，优化数据库查询 |

---

## 14. 下一步行动

**立即开始**：
1. ✅ 完成架构设计文档（当前文档）
2. ⏳ 创建项目目录结构
3. ⏳ 实现统一存储抽象层
4. ⏳ 设计并创建数据库表

**本周完成**：
- 存储层实现并通过测试
- 数据库迁移脚本就绪
- SmartEye第一个模块迁移完成（平台管理）

**需要确认**：
- [ ] 架构方案是否认可？
- [ ] 数据库设计是否需要调整？
- [ ] SmartEye模块迁移优先级是否合理？
- [ ] 是否需要提供Demo演示？
