"""Upload-only predictions reuse the durable validation queue without creating human truth."""
from io import BytesIO
import asyncio,tarfile,zipfile
from uuid import UUID,uuid4
from typing import Annotated
from typing import Literal
from fastapi import HTTPException,UploadFile
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select
from PIL import Image,ImageOps
from api.asset_routes import validate_image_upload,calculate_file_hash
from config.settings import settings
from models.validation import ValidationSet,ValidationRun,ValidationItem
from schemas.validation import CheckpointInput,ValidationConfig
from services import validation
from services.automation import model_snapshot
from services.storage import get_storage
from services import image_uploads

MAX_IMAGES=5000


def archive_images(file,kind):
    count=size=entries=expanded=0
    with image_uploads.open_archive(file,kind) as archive:
        for item in image_uploads.members(archive,kind):
            name,length,directory=image_uploads.member_info(item,kind)
            entries+=1;expanded+=length
            if length<0 or entries>settings.MAX_ARCHIVE_FILES or expanded>settings.MAX_ARCHIVE_EXPANDED_SIZE:
                raise ValueError('压缩包展开数量或大小超过限制')
            if directory or not name.lower().endswith(image_uploads.IMAGES):continue
            if length>settings.MAX_UPLOAD_SIZE:raise HTTPException(413,f'压缩包内单图超过大小限制：{name}')
            count+=1;size+=length
            if count>MAX_IMAGES:raise HTTPException(400,f'展开后最多支持{MAX_IMAGES}张图片')
            if size>settings.MAX_ARCHIVE_EXPANDED_SIZE:raise HTTPException(413,'展开后图片合计超过大小限制')
    return count,size


async def preview_images(files):
    plans=[];count=size=0
    try:
        for file in files:
            name=image_uploads.safe_name(file.filename or '')
            archive=name.lower().endswith(image_uploads.ARCHIVES)
            if archive:
                kind='zip' if name.lower().endswith('.zip') else 'tar'
                pending=asyncio.create_task(asyncio.to_thread(archive_images,file.file,kind))
                try:number,length=await asyncio.shield(pending)
                except asyncio.CancelledError:await pending;raise
            elif name.lower().endswith(image_uploads.IMAGES):number,length=1,file.size
            else:raise HTTPException(400,'请选择图片、ZIP、TAR、TAR.GZ或TGZ文件')
            count+=number;size+=length
            if count>MAX_IMAGES:raise HTTPException(400,f'展开后最多支持{MAX_IMAGES}张图片')
            if size>settings.MAX_ARCHIVE_EXPANDED_SIZE:raise HTTPException(413,'展开后图片合计超过大小限制')
            plans.append((file,name,archive))
        if not count:raise HTTPException(400,'所选文件中没有支持的图片')
        for file,name,archive in plans:
            if not archive:
                yield file;continue
            # The immutable upload was fully checked above; avoid another full gzip scan.
            iterator=image_uploads.import_items(file.file,name,0,checked=True)
            try:
                while True:
                    pending=asyncio.create_task(asyncio.to_thread(image_uploads.next_item,iterator))
                    try:item=await asyncio.shield(pending)
                    except asyncio.CancelledError:await pending;raise
                    if item is None:break
                    _,member,content,reason=item[:4]
                    if reason=='ignored':continue
                    if reason=='invalid':
                        if len(item)>5:raise HTTPException(400,item[5])
                        raise HTTPException(413,f'压缩包内单图超过大小限制：{member}')
                    with BytesIO(content) as stream:
                        yield UploadFile(filename=name+'/'+member,file=stream)
            finally:iterator.close()
    except (ValueError,zipfile.BadZipFile,tarfile.TarError,EOFError,OSError):
        raise HTTPException(400,'压缩包损坏、路径非法或含不支持的内容，请检查文件')


class PreviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)
    model_id: UUID | None = None
    checkpoint: CheckpointInput | None = None
    confidence: float = Field(default=.25,ge=0,le=1)
    iou: float = Field(default=.45,ge=0,le=1)
    labels: list[Annotated[str,Field(min_length=1,max_length=100)]] | None = Field(default=None,max_length=100)
    task_type: Literal['detection','caption','embedding'] = 'detection'
    prompt: str = Field(default='',max_length=8000)
    max_tokens: int = Field(default=512,ge=1,le=4096)
    query_text: str = Field(default='',max_length=2000)
    query_image_index: int | None = Field(default=None,ge=0,le=4999)

    @model_validator(mode='after')
    def one_model(self):
        if (self.model_id is None)==(self.checkpoint is None):raise ValueError('请选择一个检测模型来源')
        if self.checkpoint and self.labels is not None:raise ValueError('训练权重使用训练类别顺序，不支持覆盖检测标签')
        if self.task_type=='detection' and (self.prompt or self.query_text or self.query_image_index is not None):raise ValueError('检测预览不支持描述提示词或向量查询参数')
        if self.checkpoint and (self.checkpoint.kind if self.checkpoint.kind in ('caption','embedding') else 'detection')!=self.task_type:raise ValueError('预览用途与权重类型不一致')
        if self.task_type=='embedding' and bool(self.query_text)==(self.query_image_index is not None):raise ValueError('向量预览请选择文本查询或一张查询图片')
        return self


async def create(db,user,request,files):
    if not 1<=len(files)<=MAX_IMAGES:raise HTTPException(400,f'每次最多选择{MAX_IMAGES}个文件')
    if sum(file.size for file in files)>settings.MAX_ARCHIVE_UPLOAD_SIZE:raise HTTPException(413,'上传文件合计超过大小限制')
    if request.checkpoint and user.role!='admin':raise HTTPException(403,'服务器权重推理仅管理员可启动')
    id=uuid4();samples=[];size=0;storage=get_storage()
    images=preview_images(files)
    try:
        async for file in images:
            content,values=await validate_image_upload(file)
            name=image_uploads.safe_name(file.filename)
            if len(name)>255:raise HTTPException(400,'图片相对路径超过255字符，请缩短目录名后重试')
            values['original_name']=name
            with Image.open(BytesIO(content)) as picture:
                if picture.getexif().get(274,1) in range(2,9):
                    picture=ImageOps.exif_transpose(picture);output=BytesIO();picture.save(output,format=values['format'])
                    content=output.getvalue()
                    if len(content)>settings.MAX_UPLOAD_SIZE:raise HTTPException(413,'旋转后的预览图片超过单图限制')
                    values.update(width=picture.width,height=picture.height,content_hash=calculate_file_hash(content))
            size+=len(content)
            if size>settings.MAX_ARCHIVE_EXPANDED_SIZE:raise HTTPException(413,'展开后图片合计超过大小限制')
            image_id=uuid4();path=f'previews/{user.tenant_id}/{id}/{image_id}.img'
            samples.append(dict(asset_id=str(image_id),original_name=values['original_name'],blob_path=path,
                content_hash=values['content_hash'],width=values['width'],height=values['height'],truth=[]))
            await storage.save_file(BytesIO(content),path)
        if request.checkpoint:
            from services.checkpoint_validation import snapshot as checkpoint_snapshot
            snapshot=await checkpoint_snapshot(db,user,request.checkpoint,preview=True)
        else:
            from services import multimodal_validation
            snapshot=await (model_snapshot(db,request.model_id,user.tenant_id,'detection') if request.task_type=='detection' else multimodal_validation.model_snapshot(db,request.model_id,user.tenant_id,request.task_type))
            snapshot['name']=snapshot['config']['name']
        snapshot['preview']=True
        if request.task_type!='detection':snapshot['task_type']=request.task_type
        policy=ValidationConfig(inference_confidence=request.confidence,confidence=request.confidence,nms_iou=request.iou,prompt=request.prompt,max_tokens=request.max_tokens).model_dump()
        if not request.checkpoint:
            policy.update(labels=request.labels if request.labels is not None else snapshot['config']['labels'],label_mapping=snapshot['config']['label_mapping'])
        source={'kind':'upload_preview'}
        if request.task_type!='detection':source['task_type']=request.task_type
        if request.task_type=='embedding':
            query=dict(id=str(uuid4()),kind='text' if request.query_text else 'image',text=request.query_text,positives=[],negatives=[])
            if request.query_text:samples[0]['query_texts']={query['id']:request.query_text}
            else:
                if request.query_image_index>=len(samples):raise HTTPException(400,'查询图片序号超出展开后的图片数量')
                anchor=samples[request.query_image_index];query.update(asset_id=anchor['asset_id'],content_hash=anchor['content_hash'])
            source['queries']=[query]
        dataset=ValidationSet(tenant_id=user.tenant_id,user_id=user.id,project_id=None,name=samples[0]['original_name'][:180],
            labels=[],samples=samples,source=source,total=len(samples))
        db.add(dataset);await db.flush()
        run=ValidationRun(id=id,set_id=dataset.id,tenant_id=user.tenant_id,user_id=user.id,name=dataset.name,
            model_snapshot=snapshot,config=policy,total=len(samples))
        db.add(run);await db.flush()
        for sample in samples:
            db.add(ValidationItem(run_id=id,asset_id=UUID(sample['asset_id']),original_name=sample['original_name'],
                width=sample['width'],height=sample['height'],truth=[]))
        await db.commit();return run
    except BaseException:
        await db.rollback()
        for sample in samples:await storage.delete_file(sample['blob_path'])
        raise
    finally:await images.aclose()


async def scoped(db,id,user,lock=False):
    query=select(ValidationRun).where(ValidationRun.id==id,ValidationRun.tenant_id==user.tenant_id,
        ValidationRun.user_id==user.id,ValidationRun.model_snapshot['preview'].as_boolean().is_(True))
    if lock:query=query.with_for_update()
    run=await db.scalar(query)
    if run is None:raise HTTPException(404,'模型预览不存在')
    return run


async def remove(db,run):
    run=await validation.cancel_run(db,run)
    if run.model_snapshot.get('source')=='checkpoint' and run.model_snapshot['execution']['active']:
        raise HTTPException(409,'远端停止尚未确认，请重试取消后再删除预览')
    await db.refresh(run,with_for_update=True)
    if run.status in ('pending','running') or (run.model_snapshot.get('source')=='checkpoint' and run.model_snapshot['execution']['active']):
        raise HTTPException(409,'预览已重新运行，请先确认停止')
    dataset=await db.get(ValidationSet,run.set_id)
    for sample in dataset.samples:await get_storage().delete_file(sample['blob_path'])
    await db.delete(dataset);await db.commit()
