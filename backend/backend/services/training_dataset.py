"""Runs in the detection image: read-only sources, per-run configs and class labels."""
import json
import math
import os
import hashlib
from pathlib import Path

IMAGE_SUFFIXES={'.jpg','.jpeg','.png','.bmp','.webp','.tif','.tiff','.dng','.mpo'}


def _inside(root,value):
    path=(root/value).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('数据路径越过数据集目录：'+str(value))
    return path


def _description(source):
    import yaml
    path=Path(source)
    if not path.exists():raise ValueError('数据路径不存在：'+source)
    if path.is_dir():
        config=next((path/name for name in ('data.yaml','data.yml','yolo-data.yaml') if (path/name).is_file()),None)
        if config is None:
            splits={key:path/folder for key,folder in [('train','train'),('val','valid' if (path/'valid').is_dir() else 'val'),('test','test')]
                if (path/folder/'_annotations.coco.json').is_file()}
            if not {'train','val'}<=splits.keys():raise ValueError('需要包含names的YAML或train/valid COCO标注，不能猜测标签名称')
            data=json.loads((splits['train']/'_annotations.coco.json').read_text())
            categories=sorted(data['categories'],key=lambda c:c['id'])
            names=[c['name'] for c in categories]
            if not names or len(set(names))!=len(names):raise ValueError('类别名称为空或重复')
            return dict(format='coco',labels=names,splits=splits,categories=categories,config_file='')
        path=config
    if path.suffix.lower() not in ('.yaml','.yml'):raise ValueError('检测数据需YAML文件或数据集目录')
    data=yaml.safe_load(path.read_text())
    if not isinstance(data,dict):raise ValueError('YAML必须是配置对象')
    names=data.get('names')
    if isinstance(names,dict):
        names={int(k):v for k,v in names.items()}
        if sorted(names)!=list(range(len(names))):raise ValueError('类别编号必须从0连续编号')
        names=[names[i] for i in range(len(names))]
    if not isinstance(names,list) or not names or any(not isinstance(v,str) or not v.strip() for v in names) or len(set(names))!=len(names):
        raise ValueError('需要非空且不重复的names类别列表')
    if data.get('nc',len(names))!=len(names):raise ValueError('nc与names数量不一致')
    splits={key:data[key] for key in ('train','val','test') if data.get(key)}
    if not {'train','val'}<=splits.keys():raise ValueError('数据必须明确指定train和val，不自动拆分')
    if any(not isinstance(v,str) for v in splits.values()):raise ValueError('当前检测数据的每个划分需指定一个图片目录')
    declared=Path(data.get('path') or '.')
    if not declared.is_absolute():declared=path.parent/declared
    # Existing YAML may name a former container mount; use its own directory when splits resolve there.
    root=next((root for root in (declared,path.parent) if all((root/v).is_dir() for v in splits.values())),None)
    if root is None:raise ValueError('YAML中的train/val图片目录不存在；请检查路径或补充只读挂载')
    return dict(format='yolo',labels=names,splits={k:_inside(root,v) for k,v in splits.items()},config_file=str(path))


def _images(folder):
    images=sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not images:raise ValueError('图片划分为空：'+str(folder))
    for path in images:
        if not path.resolve().is_relative_to(folder.resolve()):raise ValueError('图片链接越过划分目录：'+str(path))
    return images


def inspect_detection(source):
    data=_description(source)
    counts={}
    for split,folder in data['splits'].items():
        if data['format']=='yolo':counts[split]=len(_images(folder))
        else:counts[split]=len(json.loads((folder/'_annotations.coco.json').read_text())['images'])
        if not counts[split]:raise ValueError('数据划分为空：'+split)
    return dict(labels=data['labels'],format=data['format'],splits=counts,config_file=data['config_file'])


def inspect_server_detection(source,kind):
    """Validate using installed framework path rules; never regenerate server data."""
    path=Path(source)
    if kind=='dinov3_vits16_ltdetr':return inspect_detection(source)
    if kind=='yolo26':
        from ultralytics.data.utils import check_det_dataset
        if not path.is_file():raise ValueError('YOLO请填写服务器已有YAML配置文件')
        data=check_det_dataset(str(path),autodownload=False)
        names=data['names']
        return dict(labels=list(names.values()) if isinstance(names,dict) else names,
            format='yolo',splits={key:data.get(key) for key in ('train','val','test')},config_file=str(path))
    if path.suffix.lower() in ('.yaml','.yml'):
        if path.name not in ('data.yaml','data.yml'):raise ValueError('RF-DETR配置文件须命名为data.yaml/data.yml；请使用框架支持的数据集目录')
        if not path.is_file():raise ValueError('RF-DETR配置文件不存在：'+source)
        if path.name=='data.yml' and (path.parent/'data.yaml').is_file():raise ValueError('同目录存在data.yaml，RF-DETR会优先读取它；请明确选择data.yaml或使用独立数据集目录')
        path=path.parent
    if not path.is_dir():raise ValueError('RF-DETR请填写服务器数据集目录或已有data.yaml/data.yml文件')
    config=next((path/name for name in ('data.yaml','data.yml') if (path/name).is_file()),None)
    if config:
        from rfdetr.datasets.yolo import _extract_yolo_class_names,_resolve_yolo_split_dirs
        splits={}
        for split in ('train','val'):
            images,labels=_resolve_yolo_split_dirs(path,config,split)
            if not images.is_dir() or not labels.is_dir():raise ValueError('RF-DETR数据划分目录不存在：'+split)
            splits[split]=str(images)
        return dict(labels=_extract_yolo_class_names(str(config)),format='yolo',splits=splits,config_file=str(config))
    return inspect_detection(str(path))


def lightly_detection_data(source):
    """Build Lightly input in memory; never pass the held-out test split to training."""
    data=_description(source)
    if data['format']=='yolo':
        return dict(format='yolo',path=str(Path(data['config_file']).parent),
            train=str(data['splits']['train']),val=str(data['splits']['val']),names=dict(enumerate(data['labels'])))
    return dict(format='coco',**{split:dict(images=str(data['splits'][split]),
        annotations=str(data['splits'][split]/'_annotations.coco.json')) for split in ('train','val')})


def inspect_lightly_model(source):
    import inspect
    import lightly_train
    required={'out','data','model','steps','batch_size','devices','precision','num_workers','seed',
        'model_args','save_checkpoint_args','gradient_accumulation_steps'}
    if required-inspect.signature(lightly_train.train_object_detection).parameters.keys():
        raise ValueError('镜像内Lightly Train检测训练API不兼容，请更新镜像')
    model=lightly_train.load_model(source,device='cpu')
    name=model.init_args.get('model_name')
    if name not in ('dinov3/vits16-ltdetr','dinov3/vits16-ltdetr-coco'):
        raise ValueError('需要dinov3/vits16-ltdetr-coco完整检测权重，不能使用其他型号或仅骨干权重')
    return dict(version=lightly_train.__version__,model=name)


def prepare_detection(source,destination,kind,selected_labels=None):
    data=_description(source);names=data['labels']
    selected=names if selected_labels is None else selected_labels
    if not selected or set(selected)-set(names):raise ValueError('选择的训练标签不存在或为空')
    labels=[name for name in names if name in selected]
    output=Path(destination)
    receipt=output/'class_mapping.json'
    signature=json.dumps([str(Path(source).resolve()),str(output.resolve()),kind,labels])
    if output.exists():
        # Lightning re-executes this script; only its children inherit completed preparation proof.
        rank=os.environ.get('LOCAL_RANK','')
        if (kind.startswith('rfdetr') and rank.isdecimal() and int(rank)>0 and receipt.is_file()
                and os.environ.get('PLATFORM_PREPARED_DETECTION')==signature+hashlib.sha256(receipt.read_bytes()).hexdigest()):
            return str(output)
        raise ValueError('生成数据目录已存在，禁止覆盖')
    output.mkdir(parents=True)
    mapping={old:labels.index(name) for old,name in enumerate(names) if name in labels}
    counts={};objects={split:0 for split in data['splits']}
    for split,folder in data['splits'].items():
        target=output/('valid' if split=='val' else split);target.mkdir()
        if data['format']=='yolo':
            (target/'images').mkdir();(target/'labels').mkdir()
            images=_images(folder);counts[split]=len(images)
            for index,image in enumerate(images):
                parts=list(image.parts)
                try:position=len(parts)-1-parts[::-1].index('images')
                except ValueError:raise ValueError('YOLO图片目录需包含images路径段，便于定位对应labels') from None
                parts[position]='labels';label=Path(*parts).with_suffix('.txt')
                lines=[]
                for line in (label.read_text().splitlines() if label.is_file() else []):
                    values=line.split()
                    if not values:continue
                    cid=int(values[0])
                    if len(values)!=5 or not 0<=cid<len(names) or any(not math.isfinite(float(v)) or not 0<=float(v)<=1 for v in values[1:]):
                        raise ValueError('需要有效的YOLO检测框标注：'+str(label))
                    if cid in mapping:
                        lines.append(str(mapping[cid])+' '+' '.join(values[1:]));objects[split]+=1
                name=f'{index:08d}_{image.name}'
                (target/'images'/name).symlink_to(image.resolve())
                (target/'labels'/Path(name).with_suffix('.txt')).write_text('\n'.join(lines)+('\n' if lines else ''))
        else:
            if kind=='yolo26':raise ValueError('YOLO训练需要YOLO格式数据')
            document=json.loads((folder/'_annotations.coco.json').read_text())
            categories={c['id']:c['name'] for c in document['categories']}
            if set(categories.values())!=set(names):raise ValueError('COCO各划分类别不一致')
            coco_mapping={cid:labels.index(name) for cid,name in categories.items() if name in labels}
            for index,image in enumerate(document['images']):
                original=_inside(folder,image['file_name'])
                if not original.is_file():raise ValueError('COCO图片不存在：'+str(original))
                name=f'{index:08d}_{original.name}';(target/name).symlink_to(original)
                image['file_name']=name
            annotations=[]
            for annotation in document['annotations']:
                cid=annotation['category_id']
                if cid not in categories:raise ValueError('COCO标注引用未知类别')
                if cid in coco_mapping:
                    annotation['category_id']=coco_mapping[cid];annotations.append(annotation)
            document.update(categories=[dict(id=i,name=name) for i,name in enumerate(labels)],annotations=annotations)
            (target/'_annotations.coco.json').write_text(json.dumps(document,ensure_ascii=False))
            counts[split]=len(document['images']);objects[split]=len(annotations)
    if not objects.get('train') or not objects.get('val'):raise ValueError('所选标签在train和val中都必须有标注')
    if data['format']=='yolo':
        (output/'data.yaml').write_text(json.dumps(dict(path=str(output),nc=len(labels),names=labels,
            train='train/images',val='valid/images',test='test/images' if 'test' in data['splits'] else []),ensure_ascii=False))
    receipt.write_text(json.dumps(dict(labels=labels,original_labels=names,
        id_mapping=mapping,images_by_split=counts,objects_by_split=objects),ensure_ascii=False,indent=2))
    if kind.startswith('rfdetr'):
        os.environ['PLATFORM_PREPARED_DETECTION']=signature+hashlib.sha256(receipt.read_bytes()).hexdigest()
    return str(output/'data.yaml') if kind=='yolo26' else str(output)
