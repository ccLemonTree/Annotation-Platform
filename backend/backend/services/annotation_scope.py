"""One project-level eligibility rule for annotation, inference and exports."""
from fastapi import HTTPException
from sqlalchemy import true
from models import Asset


def crops_only(project):
    return (project.label_schema or {}).get('annotation_scope','all')=='crops'


def eligible_assets(project):
    return Asset.source_type=='model_crop' if crops_only(project) else true()


def require_annotation_asset(project,asset):
    if crops_only(project) and asset.source_type!='model_crop':
        raise HTTPException(409,'此项目仅标注裁剪图，原图不参与标注，请从项目的裁剪图开始')
