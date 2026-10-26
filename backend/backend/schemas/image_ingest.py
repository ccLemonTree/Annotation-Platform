from urllib.parse import urlsplit
from pydantic import BaseModel,Field,field_validator
from schemas.prompts import SCENE_PROMPT

class ImageIngestConfig(BaseModel):
    classification_enabled:bool=False
    base_url:str='http://localhost:8000/v1'
    model:str=Field(default='Qwen3.8-27B',min_length=1,max_length=200)
    scenes:list[str]=Field(default_factory=lambda:['山上','河边','工厂','电梯','高位摄像头','低位摄像头'],min_length=1,max_length=100)
    min_long_edge:int=Field(default=0,ge=0,le=32768,strict=True)
    scene_prompt:str=Field(default=SCENE_PROMPT,min_length=1,max_length=8000)

    @field_validator('scene_prompt')
    @classmethod
    def valid_prompt(cls,value):
        value=value.strip()
        if not value:raise ValueError('场景提示词不能为空')
        return value

    @field_validator('base_url')
    @classmethod
    def valid_url(cls,value):
        value=value.strip().rstrip('/')
        parts=urlsplit(value)
        parts.port  # Validate malformed and out-of-range ports before any request.
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or any(char.isspace() for char in value):
            raise ValueError('模型地址须为不含账号、查询参数的HTTP(S)服务地址')
        return value

    @field_validator('model')
    @classmethod
    def valid_model(cls,value):
        value=value.strip()
        if not value:raise ValueError('模型名称不能为空')
        return value

    @field_validator('scenes')
    @classmethod
    def valid_scenes(cls,values):
        values=[value.strip() for value in values]
        if any(not value or len(value)>60 for value in values):raise ValueError('场景名称须为1至60个字符')
        if len(set(values))!=len(values):raise ValueError('场景名称不能重复')
        return values
