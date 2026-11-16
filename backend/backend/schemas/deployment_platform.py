from uuid import UUID
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from schemas.model_deployment import DeploymentCreate


class PlatformCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100)
    environment:Literal['docker','docker_kafka','k8s']='docker'
    control_server:str=Field(min_length=1,max_length=100)
    file_server:str=Field(default='',max_length=100)
    namespace:str=Field(default='ai',pattern=r'^[a-z0-9][a-z0-9.-]{0,62}$')
    services:list[dict]=Field(default_factory=list,max_length=50)

    @model_validator(mode='after')
    def validate_services(self):
        self.file_server=self.file_server or self.control_server
        self.services=[DeploymentCreate(**{**service,'run_id':UUID(int=0),'artifact_path':'model.plan','environment':self.environment,
            'file_server':self.file_server,'control_server':self.control_server,'model_name':'placeholder'}).model_dump(mode='json',exclude={'run_id','artifact_path','model_name','triton_config','runtime_config'}) for service in self.services]
        return self


class VersionSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    repository_id:UUID|None=None
    release_id:UUID|None=None

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.repository_id)==bool(self.release_id):raise ValueError('每项请选择一个模型版本')
        return self


class ModelAssignment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform_id:UUID
    version_key:str=Field(max_length=50)
    model_key:str=Field(pattern=r'^(?:[a-f0-9]{64}|skip)$')


class PlatformBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform_ids:list[UUID]=Field(min_length=1,max_length=50)
    versions:list[VersionSelection]=Field(min_length=1,max_length=20)
    assignments:list[ModelAssignment]=Field(default_factory=list,max_length=1000)

    @model_validator(mode='after')
    def unique(self):
        keys=[(row.repository_id,row.release_id) for row in self.versions]
        assignments=[(row.platform_id,row.version_key) for row in self.assignments]
        if len(set(self.platform_ids))!=len(self.platform_ids) or len(set(keys))!=len(keys) or len(set(assignments))!=len(assignments):raise ValueError('平台、模型版本或对应关系重复')
        return self
