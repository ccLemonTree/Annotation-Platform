from uuid import UUID,NAMESPACE_URL,uuid5
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from api.deps import get_current_superuser
from database import get_db_session
from models import ObjectAttributeTemplate
from schemas.object_attributes import AttributeTemplateCreate

router=APIRouter()
BUILTIN_IDS=('builtin_vehicle','builtin_person','builtin_object')


def builtin_ids(tenant_id):
    return {uuid5(NAMESPACE_URL,f'attribute-template/{tenant_id}/{name}'):name for name in BUILTIN_IDS}


def summary(row):return {'id':builtin_ids(row.tenant_id).get(row.id,str(row.id)),'name':row.name,'config':row.config}


async def commit_template(db,row):
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'模板名称已存在或模板已更新，请刷新后重试') from None
    return summary(row)


@router.get('')
async def listing(db=Depends(get_db_session),user=Depends(get_current_superuser)):
    rows=(await db.scalars(select(ObjectAttributeTemplate).where(ObjectAttributeTemplate.tenant_id==user.tenant_id).order_by(ObjectAttributeTemplate.name,ObjectAttributeTemplate.id))).all()
    return [summary(row) for row in rows]


@router.post('',status_code=201)
async def create(request:AttributeTemplateCreate,db=Depends(get_db_session),user=Depends(get_current_superuser)):
    row=ObjectAttributeTemplate(tenant_id=user.tenant_id,name=request.name,config=request.config.model_dump())
    db.add(row)
    return await commit_template(db,row)


@router.put('/{template_id}')
async def update(template_id:str,request:AttributeTemplateCreate,db=Depends(get_db_session),user=Depends(get_current_superuser)):
    builtin=template_id in BUILTIN_IDS
    if builtin:
        row_id=next(key for key,name in builtin_ids(user.tenant_id).items() if name==template_id)
    else:
        try:row_id=UUID(template_id)
        except ValueError:raise HTTPException(404,'模板不存在') from None
    row=await db.scalar(select(ObjectAttributeTemplate).where(ObjectAttributeTemplate.id==row_id,
        ObjectAttributeTemplate.tenant_id==user.tenant_id).with_for_update())
    if row is None:
        if not builtin:raise HTTPException(404,'模板不存在')
        row=ObjectAttributeTemplate(id=row_id,tenant_id=user.tenant_id)
        db.add(row)
    row.name=request.name
    row.config=request.config.model_dump()
    return await commit_template(db,row)


@router.delete('/{template_id}',status_code=204)
async def remove(template_id:UUID,db=Depends(get_db_session),user=Depends(get_current_superuser)):
    row=await db.scalar(select(ObjectAttributeTemplate).where(ObjectAttributeTemplate.id==template_id,ObjectAttributeTemplate.tenant_id==user.tenant_id))
    if row is None or template_id in builtin_ids(user.tenant_id):raise HTTPException(404,'模板不存在')
    await db.delete(row);await db.commit()
    return Response(status_code=204)
