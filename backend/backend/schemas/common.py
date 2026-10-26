"""
通用响应模型和分页模型
"""
from pydantic import BaseModel, Field
from typing import Generic, TypeVar, Optional


T = TypeVar('T')


class ResponseModel(BaseModel, Generic[T]):
    """统一响应包装"""
    success: bool = True
    data: Optional[T] = None
    message: Optional[str] = None
    code: int = 200


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")

    @property
    def skip(self) -> int:
        """跳过的记录数"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """限制返回的记录数"""
        return self.page_size

    @property
    def offset(self) -> int:
        """偏移量（同skip）"""
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应"""
    total: int = Field(..., description="总记录数")
    items: list[T] = Field(..., description="数据列表")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")

    @property
    def total_pages(self) -> int:
        """总页数"""
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        """是否有下一页"""
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        """是否有上一页"""
        return self.page > 1


class BatchOperationResponse(BaseModel):
    """批量操作响应"""
    success: bool = Field(..., description="操作是否成功")
    processed_count: int = Field(..., description="成功处理的数量")
    failed_count: int = Field(0, description="失败的数量")
    message: str = Field(..., description="操作结果消息")
