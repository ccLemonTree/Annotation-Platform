from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchRegion(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class SmartSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    platform_id: str = Field(min_length=1, max_length=100)
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    reference_query_id: UUID | None = None
    reference_asset_id: UUID | None = None
    region: SearchRegion | None = None
    limit: int = Field(default=20, ge=1, le=100)
    start_time: datetime
    end_time: datetime

    @model_validator(mode='after')
    def query(self):
        if self.start_time.tzinfo or self.end_time.tzinfo:
            raise ValueError('请使用平台当地时间，不附加时区')
        if self.end_time < self.start_time:
            raise ValueError('结束时间不能早于开始时间')
        reference=(self.reference_query_id,self.reference_asset_id,self.region)
        if self.text is not None:
            if any(value is not None for value in reference):raise ValueError('文本查询不能同时包含图片查询')
        elif any(value is None for value in reference):
            raise ValueError('请从智搜结果选择参考图片并框选目标')
        return self
