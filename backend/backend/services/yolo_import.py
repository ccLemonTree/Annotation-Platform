"""YOLO detection archives; explicit negatives, no replacement of project annotations."""
import math,zipfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime,timezone
from pathlib import PurePosixPath
from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, select
import yaml
from models import Project,Annotation,User,LibraryImage,Asset
from services.annotation_scope import crops_only
from services.image_uploads import check_archive,open_archive,member_info,IMAGES


async def project_scope(db,id,tenant_id):
    project=await db.scalar(select(Project).where(Project.id==id,Project.tenant_id==tenant_id).with_for_update())
    if project is None:raise HTTPException(404,'标注目标项目不存在')
    if project.task_type!='detection':raise HTTPException(400,'YOLO导入只支持目标检测项目')
    return project


def text(file,row):
    if row.file_size>1024**2:raise ValueError('YOLO类别或标注文件超过1MiB')
    return file.read(row).decode('utf-8-sig')


def names_from(value):
    if isinstance(value,dict):
        try:
            ids={int(key):name for key,name in value.items()}
            if set(ids)!=set(range(len(ids))):raise ValueError()
            value=[ids[i] for i in range(len(ids))]
        except (ValueError,TypeError):raise ValueError('YOLO类别编号必须从0连续排列') from None
    if not isinstance(value,list) or not 1<=len(value)<=100 or any(not isinstance(v,str) or not v.strip() or len(v)>100 or v=='背景' for v in value) or len(set(value))!=len(value):
        raise ValueError('YOLO类别表必须包含1至100个不重复名称')
    return value


def read_names(filename,content):
    if filename in ('data.yaml','data.yml'):
        config=yaml.safe_load(content)
        return names_from(config.get('names') if isinstance(config,dict) else None)
    if filename=='classes.txt':return names_from(content.splitlines())
    raise ValueError('类别表须使用 data.yaml、data.yml 或 classes.txt')


def parse_boxes(content,names):
    boxes=[]
    for line in content.splitlines():
        if not line.strip():continue
        parts=line.split()
        if len(parts)!=5 or not parts[0].isdigit():raise ValueError('仅支持YOLO检测标注：class x_center y_center width height')
        id=int(parts[0]);x,y,w,h=map(float,parts[1:])
        # Some exporters retain a few pixels outside the image; clip up to 1% per edge.
        if (names is not None and id>=len(names)) or any(not math.isfinite(v) or not 0<=v<=1 for v in (x,y,w,h)) or w<=0 or h<=0 or min(x-w/2,y-h/2)<-.01 or max(x+w/2,y+h/2)>1.01:
            raise ValueError('YOLO类别编号或归一化框坐标无效')
        x1=max(0,x-w/2);y1=max(0,y-h/2);x2=min(1,x+w/2);y2=min(1,y+h/2)
        category={'label':names[id]} if names is not None else {'label':f'待设置类别（YOLO {id}）','yolo_class_id':id}
        boxes.append(dict(**category,x=x1,y=y1,width=x2-x1,height=y2-y1))
        if len(boxes)>5000:raise ValueError('单图标注超过5000个框')
    return boxes


def import_items(path,filename,cursor,*,on_total=None):
    if not filename.lower().endswith('.zip'):raise ValueError('YOLO导入只支持ZIP包')
    check_archive(path,'zip')
    with open_archive(path,'zip') as file:
        rows={}
        for row in file.infolist():
            name,_,directory=member_info(row,'zip');name=str(PurePosixPath(name))
            if name in rows:raise ValueError('YOLO包包含重复路径')
            if not directory:rows[name]=row
        tables=[]
        for name,row in rows.items():
            base=PurePosixPath(name).name.lower()
            if base in ('data.yaml','data.yml'):
                config=yaml.safe_load(text(file,row))
                if isinstance(config,dict) and 'names' in config:tables.append(names_from(config['names']))
            elif base=='classes.txt':tables.append(read_names(base,text(file,row)))
        if not tables or any(table!=tables[0] for table in tables):raise ValueError('请提供一致的data.yaml names或classes.txt类别顺序')
        names=tables[0];images=[name for name in rows if name.lower().endswith(IMAGES)]
        if not images:raise ValueError('YOLO包中没有图片')
        annotations={};used={};errors={}
        for name in images:
            path=PurePosixPath(name);parts=list(path.with_suffix('.txt').parts)
            candidates=[str(path.with_suffix('.txt'))]
            if 'images' in parts:
                parts[parts.index('images')]='labels';candidates.insert(0,str(PurePosixPath(*parts)))
            matches=[key for key in dict.fromkeys(candidates) if key in rows]
            if len(matches)!=1:
                errors[name]=name+'：缺少或存在歧义的同名TXT，负样本须提供空TXT';continue
            label=matches[0]
            if label in used:
                errors[name]=errors[used[label]]=label+'：同一TXT匹配多张图片，已跳过歧义图片';continue
            used[label]=name
            annotations[name]=label
        orphans=[name for name in rows if 'labels' in PurePosixPath(name).parts and name.lower().endswith('.txt') and PurePosixPath(name).name!='classes.txt' and name not in used]
        if on_total:on_total(len(images)+len(orphans))
        for index,name in enumerate(images):
            if index<cursor:continue
            if name in errors:yield index,name,None,'invalid',None,errors[name];continue
            label=annotations[name]
            try:data=dict(names=names,boxes=parse_boxes(text(file,rows[label]),names))
            except (ValueError,UnicodeError,zipfile.BadZipFile,EOFError,OSError) as error:
                yield index,name,None,'invalid',None,label+'：'+str(error);continue
            row=rows[name]
            from config.settings import settings
            if row.file_size>settings.MAX_UPLOAD_SIZE:yield index,name,None,'invalid',None;continue
            try:content=file.read(row)
            except (zipfile.BadZipFile,EOFError,OSError):yield index,name,None,'invalid',None,name+'：图片解压读取失败';continue
            yield index,name,content,None,data
        for index,name in enumerate(orphans,len(images)):
            if index>=cursor:yield index,name,None,'ignored',None,name+'：标注未匹配图片，已忽略'


def pixel_boxes(image,boxes):
    return [dict(box,x=box['x']*image.width,y=box['y']*image.height,
        width=min(box['width']*image.width,image.width-box['x']*image.width),height=min(box['height']*image.height,image.height-box['y']*image.height)) for box in boxes]


async def read_label(file):
    content=await file.read(1024**2+1)
    if len(content)>1024**2:raise ValueError('YOLO类别或标注文件超过1MiB')
    return content.decode('utf-8-sig')


CLASS_FILES=('data.yaml','data.yml','classes.txt')

async def import_label_files(db,project,classes,files,archive,actor_id,batch_id=None):
    from config.settings import settings

    if project.task_type!='detection':raise HTTPException(400,'YOLO导入只支持目标检测项目')
    try:
        names=read_names((classes.filename or '').lower(),await read_label(classes)) if classes else None
        if not archive:
            if not files:raise ValueError('请选择 TXT 标签文件')
            return {'items':await apply_label_files(db,project,names,files,actor_id,batch_id)}
        if files or not (archive.filename or '').lower().endswith('.zip'):
            raise ValueError('请选择单独的标签 ZIP 或一组 TXT 标签文件')
        if archive.size is not None and archive.size>settings.MAX_IMAGE_ARCHIVE_UPLOAD_SIZE:
            raise HTTPException(413,'压缩包超过上传大小限制')
        await run_in_threadpool(check_archive,archive.file,'zip')
        with open_archive(archive.file,'zip') as package:
            members=[(PurePosixPath(member_info(entry,'zip')[0]).name,entry) for entry in package.infolist() if not entry.is_dir()]
            if names is None:
                tables=[read_names(name.lower(),await run_in_threadpool(text,package,entry))
                    for name,entry in members if name.lower() in CLASS_FILES]
                if tables:
                    if any(table!=tables[0] for table in tables):
                        raise ValueError('标签 ZIP 内的类别表不一致')
                    names=tables[0]
            labels=[(name,entry) for name,entry in members if name.lower().endswith('.txt') and name.lower() not in CLASS_FILES]
            if not labels:raise ValueError('标签 ZIP 中没有 TXT 标签文件')
            stems=Counter(PurePosixPath(name.lower()).stem for name,_ in labels)
            items=[]
            for start in range(0,len(labels),64):
                batch=[]
                for name,entry in labels[start:start+64]:
                    if stems[PurePosixPath(name.lower()).stem]>1:
                        items.append(dict(filename=name,status='conflict',message='ZIP 中有同名标签文件'));continue
                    if entry.file_size>1024**2:
                        items.append(dict(filename=name,status='invalid',message='YOLO标注文件超过1MiB'));continue
                    batch.append((name,entry))
                if batch:
                    with ExitStack() as opened:
                        uploads=[UploadFile(opened.enter_context(package.open(entry)),filename=name) for name,entry in batch]
                        items.extend(await apply_label_files(db,project,names,uploads,actor_id,batch_id))
            return {'items':items}
    except (ValueError,yaml.YAMLError,zipfile.BadZipFile,NotImplementedError,EOFError) as exc:
        raise HTTPException(422,('YOLO标签导入失败：'+str(exc))[:500]) from exc


async def apply_label_files(db,project,names,files,actor_id,batch_id):
    from services.annotations import save_annotation

    stems=Counter(PurePosixPath((file.filename or '').lower()).stem for file in files)
    filenames={stem+extension for stem in stems for extension in IMAGES}
    matches=defaultdict(list)
    for asset in (await db.scalars(select(Asset).where(Asset.project_id==project.id,
        func.lower(Asset.original_name).in_(filenames)).order_by(Asset.id).with_for_update())).all():
        matches[PurePosixPath(asset.original_name.lower()).stem].append(asset)
    project=await project_scope(db,project.id,project.tenant_id)
    existing={row.asset_id:row for row in (await db.scalars(select(Annotation).where(
        Annotation.asset_id.in_([asset.id for rows in matches.values() for asset in rows]),
        Annotation.is_latest.is_(True)))).all()}
    items=[]
    for file in files:
        filename=file.filename or ''
        path=PurePosixPath(filename.lower())
        assets=matches[path.stem]
        row={'filename':filename}
        items.append(row)
        if path.suffix!='.txt' or '/' in filename or '\\' in filename or len(filename)>255:
            row.update(status='invalid',message='请选择独立的 TXT 标签文件');continue
        if stems[path.stem]>1 or len(assets)>1:
            row.update(status='conflict',message='有同名标签文件或同名图片，无法唯一匹配');continue
        if not assets:
            row.update(status='missing',message='项目中找不到同名图片');continue
        asset=assets[0]
        row['asset_id']=str(asset.id)
        if crops_only(project) and asset.source_type!='model_crop':
            row.update(status='conflict',message='当前项目只标注裁剪图');continue
        try:boxes=pixel_boxes(asset,parse_boxes(await read_label(file),names))
        except (ValueError,zipfile.BadZipFile,NotImplementedError,EOFError) as exc:
            row.update(status='invalid',message=str(exc)[:500]);continue
        previous=existing.get(asset.id)
        if previous:
            if previous.annotation_data.get('boxes')==boxes:row['status']='unchanged'
            else:row.update(status='conflict',message='图片已有标注')
            continue
        labels=(project.label_schema or {}).get('labels') or []
        if names is not None and labels and any(box['label'] not in labels for box in boxes):
            row.update(status='conflict',message='标签文件使用了项目未配置的类别');continue
        if names is not None and not labels:
            if project.annotated_count:
                row.update(status='conflict',message='项目已有标注，请先配置匹配的类别表');continue
            project.label_schema={**(project.label_schema or {}),'labels':names}
            await db.flush()
        # External keys have no user account. Zero identifies a system-generated audit event.
        provenance={'source':'yolo_labels','filename':filename,'imported_by':actor_id,'source_labels':names}
        if batch_id is not None and names is None:
            provenance['batch_id']=str(batch_id)
        await save_annotation(db,asset,{'boxes':boxes,'import_source':provenance},0,actor_id or 0,source='import_yolo')
        row['status']='imported'
    await db.commit()
    return items


async def save(db,job,image,data,result):
    actor=await db.get(User,job.user_id)
    if actor is None or not actor.is_active or actor.tenant_id!=job.tenant_id:raise ValueError('上传用户已停用或租户变化')
    if job.trust_annotations and actor.role!='admin':raise ValueError('信任导入标注的管理员权限已变化')
    boxes=pixel_boxes(image,data['boxes'])
    existing=image.imported_annotation
    same=existing is not None and existing['boxes']==boxes and existing['names']==data['names']
    if existing and (not same or not (job.trust_annotations and not existing.get('trusted'))):
        key='annotation_skipped' if same else 'conflicts'
        result[key]=result.get(key,0)+1
        if key=='conflicts' and len(result['errors'])<20:result['errors'].append(image.original_name+'：图库已有导入标注不同，保留原来源')
    else:
        image.imported_annotation=dict(format='yolo',boxes=boxes,names=data['names'],trusted=job.trust_annotations,
            trusted_by=job.user_id if job.trust_annotations else None,imported_by=job.user_id,imported_at=datetime.now(timezone.utc).isoformat())
        result['annotated']=result.get('annotated',0)+1


async def copy_to_project(db,assets,user):
    from services.annotations import save_annotation,record_event,sync_state
    from services.image_library import annotation_for_project
    project=await project_scope(db,assets[0].project_id,user.tenant_id)
    rows=[(asset,await db.get(LibraryImage,asset.image_id)) for asset in assets]
    sources=[(asset,image) for asset,image in rows if image.imported_annotation is not None]
    result=dict(annotated=0,annotation_skipped=0,conflicts=0,without_annotations=len(rows)-len(sources))
    labels=(project.label_schema or {}).get('labels',[])
    required=list(dict.fromkeys(name for _,image in sources for name in image.imported_annotation['names']))
    if required and not labels:
        if project.annotated_count:raise HTTPException(400,'目标项目已有标注，请先配置匹配的类别表')
        project.label_schema={**(project.label_schema or {}),'labels':required}
        labels=required
    for asset,image in sources:
        source=image.imported_annotation
        existing=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id).order_by(Annotation.version.desc()).limit(1))
        if existing:
            result['annotation_skipped' if existing.is_latest and existing.annotation_data.get('boxes')==source['boxes'] else 'conflicts']+=1
            continue
        data,filtered=annotation_for_project(project,{'boxes':source['boxes']})
        if data is None:
            result['annotation_skipped']+=1;continue
        approval_reused=bool(source.get('trusted')) and not filtered and set(labels).issubset(source['names'])
        provenance=dict(image_id=str(image.id),imported_by=source['imported_by'],imported_at=source['imported_at'],
            trusted_by=source.get('trusted_by'),filtered=filtered,approval_reused=approval_reused,
            source_labels=source['names'],target_labels=labels)
        annotation=await save_annotation(db,asset,{**data,'import_source':provenance},0,user.id,source='import_yolo')
        if approval_reused:
            annotation.review_status='approved';annotation.reviewer_id=source['trusted_by'];annotation.reviewed_at=datetime.utcnow()
            annotation.review_comment='沿用管理员在YOLO导入时的信任确认，无需逐图复核'
            record_event(db,asset,user.id,'approve',dict(source='import_yolo',annotation_id=str(annotation.id),version=annotation.version,import_source=provenance))
            await sync_state(db,asset)
        elif filtered or set(labels)-set(source['names']):
            annotation.review_comment='按目标项目标签带入，标签范围或内容变化，需重新审核完整性'
        result['annotated']+=1
    return result
