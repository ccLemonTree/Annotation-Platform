from urllib.parse import urlsplit
from pydantic import BaseModel,Field,field_validator,model_validator
from schemas.prompts import SCENE_PROMPT

DEFAULT_SCENE_GROUPS={
    '交通场景':['道路','路口','停车场','隧道','桥梁','公交站'],
    '自然环境':['山上','河边','林区','湖泊','水库','海边'],
    '生产作业':['工厂','建筑工地','仓库','园区','农田'],
    '公共区域':['广场','公园','景区','学校','医院'],
    '居住商业':['小区','商铺','商场','农贸市场'],
    '室内空间':['电梯','大厅','走廊','楼梯间','地下车库'],
    '摄像机位':['高位摄像头','低位摄像头'],
}

class SceneGroup(BaseModel):
    name:str=Field(min_length=1,max_length=40)
    scenes:list[str]=Field(min_length=1,max_length=100)

    @field_validator('name')
    @classmethod
    def valid_name(cls,value):
        value=value.strip()
        if not value:raise ValueError('场景大类不能为空')
        return value

    @field_validator('scenes')
    @classmethod
    def valid_children(cls,values):
        values=[value.strip() for value in values]
        if any(not value or len(value)>60 or value.startswith('@group:') for value in values):raise ValueError('具体场景须为1至60个字符，不能使用保留前缀')
        if len(set(values))!=len(values):raise ValueError('具体场景不能重复')
        return values

class ImageIngestConfig(BaseModel):
    classification_enabled:bool=False
    base_url:str='http://localhost:8000/v1'
    model:str=Field(default='Qwen3.8-27B',min_length=1,max_length=200)
    scenes:list[str]=Field(default_factory=lambda:[scene for values in DEFAULT_SCENE_GROUPS.values() for scene in values],min_length=1,max_length=100)
    scene_groups:list[SceneGroup]|None=Field(default=None,max_length=30)
    min_long_edge:int=Field(default=0,ge=0,le=32768,strict=True)
    scene_prompt:str=Field(default=SCENE_PROMPT,min_length=1,max_length=8000)

    @model_validator(mode='after')
    def group_scenes(self):
        groups={}
        assigned=set()
        for group in self.scene_groups or []:
            if group.name in groups:raise ValueError('场景大类名称不能重复')
            if assigned.intersection(group.scenes):raise ValueError('同一具体场景只能属于一个大类')
            assigned.update(group.scenes)
            groups[group.name]=[scene for scene in group.scenes if scene in self.scenes]
        if len(assigned)>100:raise ValueError('具体场景最多100个')
        assigned={scene for values in groups.values() for scene in values}
        for scene in self.scenes:
            if scene not in assigned:
                name=next((name for name,values in DEFAULT_SCENE_GROUPS.items() if scene in values),'其他场景')
                groups.setdefault(name,[]).append(scene)
        self.scene_groups=[SceneGroup(name=name,scenes=values) for name,values in groups.items() if values]
        return self

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
        if any(not value or len(value)>60 or value.startswith('@group:') for value in values):raise ValueError('场景名称须为1至60个字符，不能使用保留前缀')
        if len(set(values))!=len(values):raise ValueError('场景名称不能重复')
        return values
