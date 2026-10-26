# 🔌 API 开发指南

如何基于现有脚手架快速开发新的API接口。

## 目录结构约定

```
api/
├── __init__.py           # 导出所有路由
├── deps.py               # 依赖注入（数据库会话、认证等）
├── smarteye_routes.py    # SmartEye相关接口
├── project_routes.py     # 项目管理接口
├── annotation_routes.py  # 标注管理接口
└── training_routes.py    # 训练管理接口
```

## 完整开发流程示例

### 示例：实现项目管理的完整CRUD

#### 1. 定义 Pydantic Schema (schemas/project.py)

```python
"""
项目相关的数据模型
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime
from models.project import ProjectStatus, TaskType


# 创建项目请求
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="项目名称")
    task_type: TaskType = Field(..., description="任务类型")
    description: Optional[str] = Field(None, description="项目描述")
    classes: list[str] = Field(..., min_items=1, description="分类标签列表")


# 更新项目请求
class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[ProjectStatus] = None
    classes: Optional[list[str]] = None


# 项目响应
class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    tenant_id: int
    name: str
    task_type: TaskType
    status: ProjectStatus
    description: Optional[str]
    classes: list[str]
    total_assets: int
    annotated_count: int
    reviewed_count: int
    created_at: datetime
    updated_at: datetime


# 项目列表响应
class ProjectListResponse(BaseModel):
    total: int
    items: list[ProjectResponse]
    page: int
    page_size: int
```

#### 2. 实现路由处理器 (api/project_routes.py)

```python
"""
项目管理API路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from typing import List

from config.database import get_db
from models.project import Project
from schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListResponse


router = APIRouter()


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    project: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    # 后续添加：current_user = Depends(get_current_user)
):
    """
    创建新项目
    
    - **name**: 项目名称（必填）
    - **task_type**: 任务类型（DETECTION/CLASSIFICATION/SEGMENTATION/OCR）
    - **description**: 项目描述（可选）
    - **classes**: 分类标签列表（必填，至少一个）
    """
    # 检查项目名称是否重复
    stmt = select(Project).where(
        Project.name == project.name,
        # Project.tenant_id == current_user.tenant_id  # 租户隔离
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="项目名称已存在")
    
    # 创建项目
    db_project = Project(
        tenant_id=1,  # 临时硬编码，后续从 current_user 获取
        name=project.name,
        task_type=project.task_type,
        description=project.description,
        classes=project.classes,
    )
    
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    
    return db_project


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="状态筛选"),
    search: Optional[str] = Query(None, description="名称搜索"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取项目列表（分页）
    
    支持筛选和搜索：
    - **page**: 页码（从1开始）
    - **page_size**: 每页数量（最大100）
    - **status**: 状态筛选（ACTIVE/PAUSED/COMPLETED/ARCHIVED）
    - **search**: 名称模糊搜索
    """
    # 构建查询
    query = select(Project).where(Project.tenant_id == 1)  # 租户隔离
    
    if status:
        query = query.where(Project.status == status)
    
    if search:
        query = query.where(Project.name.ilike(f"%{search}%"))
    
    # 获取总数
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)
    
    # 分页查询
    query = query.order_by(Project.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    result = await db.execute(query)
    projects = result.scalars().all()
    
    return ProjectListResponse(
        total=total,
        items=projects,
        page=page,
        page_size=page_size
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    获取项目详情
    """
    stmt = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == 1  # 租户隔离
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    project_update: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    更新项目信息
    
    支持部分更新，只传需要修改的字段
    """
    # 查询项目
    stmt = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == 1
    )
    result = await db.execute(stmt)
    db_project = result.scalar_one_or_none()
    
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 更新字段
    update_data = project_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_project, field, value)
    
    await db.commit()
    await db.refresh(db_project)
    
    return db_project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    删除项目
    
    注意：会级联删除相关的资产和标注数据
    """
    stmt = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == 1
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    await db.delete(project)
    await db.commit()
    
    return None


@router.get("/{project_id}/stats")
async def get_project_stats(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    获取项目统计信息
    
    返回：总资产数、已标注数、已复核数、标注进度等
    """
    stmt = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == 1
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    return {
        "project_id": project.id,
        "total_assets": project.total_assets,
        "annotated_count": project.annotated_count,
        "reviewed_count": project.reviewed_count,
        "annotation_progress": round(project.annotated_count / project.total_assets * 100, 2) if project.total_assets > 0 else 0,
        "review_progress": round(project.reviewed_count / project.total_assets * 100, 2) if project.total_assets > 0 else 0,
    }
```

#### 3. 创建 schemas 目录

```bash
mkdir schemas
touch schemas/__init__.py
touch schemas/project.py
```

#### 4. 在 main.py 中注册路由（已完成）

```python
app.include_router(
    project_routes.router,
    prefix="/api/projects",
    tags=["项目管理"]
)
```

## 通用模式和最佳实践

### 1. 依赖注入

创建 `api/deps.py`：

```python
"""
通用依赖注入
"""
from typing import AsyncGenerator
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db


# OAuth2认证（后续实现）
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
):
    """
    获取当前登录用户
    """
    # TODO: 实现JWT验证
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未实现认证"
    )


async def get_current_tenant(
    current_user = Depends(get_current_user)
):
    """
    获取当前租户ID
    """
    return current_user.tenant_id
```

### 2. 统一错误处理

在 `main.py` 添加：

```python
from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # 生产环境应记录日志
    return JSONResponse(
        status_code=500,
        content={"detail": "内部服务器错误"}
    )
```

### 3. 统一响应格式

创建 `schemas/common.py`：

```python
"""
通用响应模型
"""
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional


T = TypeVar('T')


class ResponseModel(BaseModel, Generic[T]):
    """统一响应包装"""
    success: bool = True
    data: Optional[T] = None
    message: Optional[str] = None
    code: int = 200


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应"""
    total: int
    items: list[T]
    page: int
    page_size: int
    has_more: bool
    
    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size
```

### 4. 查询参数复用

创建通用查询参数类：

```python
from fastapi import Query


class PaginationParams:
    """分页参数"""
    def __init__(
        self,
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(20, ge=1, le=100, description="每页数量")
    ):
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size


# 使用示例
@router.get("/")
async def list_items(
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db)
):
    query = select(Item).offset(pagination.offset).limit(pagination.page_size)
    ...
```

## 测试API

### 使用 curl

```bash
# 创建项目
curl -X POST http://localhost:8000/api/projects/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "测试项目",
    "task_type": "DETECTION",
    "description": "这是一个测试项目",
    "classes": ["person", "car", "dog"]
  }'

# 获取项目列表
curl http://localhost:8000/api/projects/?page=1&page_size=10

# 获取项目详情
curl http://localhost:8000/api/projects/1

# 更新项目
curl -X PATCH http://localhost:8000/api/projects/1 \
  -H "Content-Type: application/json" \
  -d '{"status": "PAUSED"}'

# 删除项目
curl -X DELETE http://localhost:8000/api/projects/1
```

### 使用 httpie

```bash
# 安装: pip install httpie

# 创建项目
http POST http://localhost:8000/api/projects/ \
  name="测试项目" \
  task_type="DETECTION" \
  classes:='["person","car"]'

# 获取列表
http GET http://localhost:8000/api/projects/ page==1 page_size==10
```

## 开发检查清单

开发新API时的检查清单：

- [ ] Schema定义完整（Create/Update/Response）
- [ ] 路由处理器实现CRUD
- [ ] 添加输入验证和错误处理
- [ ] 实现租户隔离
- [ ] 添加分页和筛选
- [ ] 编写API文档字符串
- [ ] 添加单元测试
- [ ] 在Swagger文档中验证
- [ ] 检查SQL查询性能

## 下一步

1. 实现认证授权系统
2. 完善所有路由的实现
3. 添加单元测试和集成测试
4. 优化数据库查询性能

---

**提示**: 使用 FastAPI 的自动文档 (http://localhost:8000/docs) 进行快速测试和调试。
