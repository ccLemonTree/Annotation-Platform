from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import get_tenant_id
from database import get_db_session
from models.project_work import ProjectWorkflowTemplate
from schemas.project_work import WorkflowTemplateCreate

router = APIRouter()


def response(row):
    return dict(id=str(row.id),name=row.name,**row.config)


async def scoped(db,tenant_id,template_id):
    row=await db.scalar(select(ProjectWorkflowTemplate).where(ProjectWorkflowTemplate.id==template_id,
        ProjectWorkflowTemplate.tenant_id==tenant_id).with_for_update())
    if row is None:raise HTTPException(404,'模板不存在')
    return row


async def save(db,row,request):
    if any(step.operation=='attributes' and not step.attribute_templates for step in request.steps) and not request.attribute_schema:
        raise HTTPException(400,'请先配置目标与属性')
    row.name=request.name
    row.config=request.model_dump(mode='json',exclude={'name'})
    db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409,'模板名称已存在') from None
    return response(row)


@router.get('')
async def listing(tenant_id=Depends(get_tenant_id),db=Depends(get_db_session)):
    rows=(await db.scalars(select(ProjectWorkflowTemplate).where(ProjectWorkflowTemplate.tenant_id==tenant_id)
        .order_by(ProjectWorkflowTemplate.name,ProjectWorkflowTemplate.id))).all()
    return [response(row) for row in rows]


@router.post('',status_code=201)
async def create(request:WorkflowTemplateCreate,tenant_id=Depends(get_tenant_id),db=Depends(get_db_session)):
    return await save(db,ProjectWorkflowTemplate(tenant_id=tenant_id),request)


@router.put('/{template_id}')
async def update(template_id:UUID,request:WorkflowTemplateCreate,tenant_id=Depends(get_tenant_id),db=Depends(get_db_session)):
    return await save(db,await scoped(db,tenant_id,template_id),request)


@router.delete('/{template_id}',status_code=204)
async def remove(template_id:UUID,tenant_id=Depends(get_tenant_id),db=Depends(get_db_session)):
    await db.delete(await scoped(db,tenant_id,template_id))
    await db.commit()
    return Response(status_code=204)
