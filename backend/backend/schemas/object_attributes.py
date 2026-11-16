import math
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from typing import Literal


class AttributeField(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    name:str=Field(min_length=1,max_length=60)
    instruction:str=Field(default='',max_length=500)
    options:list[str]=Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def choices(self):
        if any(not value.strip() or len(value)>100 for value in self.options):raise ValueError('可选值须为1至100字符')
        self.options=[value.strip() for value in self.options]
        if len(set(self.options))!=len(self.options):raise ValueError('属性可选值不能重复')
        return self


class AttributeSchema(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    target_label:str|list[str]=Field(min_length=1,max_length=100)
    fields:list[AttributeField]=Field(min_length=1,max_length=30)

    @model_validator(mode='after')
    def unique(self):
        if any(not label.strip() or len(label)>100 for label in self.target_labels):raise ValueError('检测目标标签须为1至100字符')
        if len(set(self.target_labels))!=len(self.target_labels):raise ValueError('检测目标标签不能重复')
        for key in ('key','name'):
            values=[getattr(field,key) for field in self.fields]
            if len(set(values))!=len(values):raise ValueError('属性标识和名称不能重复')
        return self

    @property
    def target_labels(self):
        return self.target_label if isinstance(self.target_label,list) else [self.target_label]


class AttributeTemplateCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=60)
    config:AttributeSchema


class AttributeValue(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    value:str|None=Field(max_length=500)
    reason:str=Field(default='',max_length=500)
    probabilities:dict[str,float]|None=Field(default=None,min_length=1,max_length=100)

    @field_validator('probabilities',mode='before')
    @classmethod
    def valid_probabilities(cls,value):
        if value is not None and (not isinstance(value,dict) or any(
            not isinstance(key,str) or not key.strip() or len(key)>100 or isinstance(prob,bool) or
            not isinstance(prob,(int,float)) or not math.isfinite(prob) or not 0<=prob<=1 for key,prob in value.items())):
            raise ValueError('候选概率必须为0至1的有限数字')
        return value

    @model_validator(mode='after')
    def unknown(self):
        if self.value is not None and not self.value:raise ValueError('空属性须保存为null')
        if self.value is None and not self.reason:raise ValueError('无法判断须填写原因')
        return self


class AttributeFilters(BaseModel):
    attribute_key:str|None=Field(default=None,pattern=r'^[a-z][a-z0-9_]{0,49}$')
    attribute_value:str|None=Field(default=None,min_length=1,max_length=500)
    attribute_state:Literal['all','analyzed','known','unknown','missing']='all'
    review_status:Literal['pending','approved','rejected']|None=None
