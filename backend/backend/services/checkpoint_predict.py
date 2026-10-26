"""Runs inside the isolated validation container, loading weights once for the batch."""
import hashlib
import json
import os
from pathlib import Path
import traceback


def sha(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def predict_batch(root='/job'):
    root=Path(root);config=json.loads((root/'config.json').read_text());manifest=json.loads((root/'input/manifest.json').read_text())
    weight='/weights/model'+Path(config['weight_path']).suffix
    if sha(weight)!=config['weight_sha256']:raise ValueError('权重哈希不一致')
    device='cpu' if config['device']=='cpu' else 'cuda:0'
    names=config['class_names'];policy=config['policy']
    if config['kind']=='yolo26':
        from ultralytics import YOLO
        model=YOLO(weight)
        if getattr(model,'task',None)!='detect':raise ValueError('仅支持目标检测权重')
        actual=[model.names[index] for index in range(len(model.names))]
        if names and names!=actual:raise ValueError('类别顺序与YOLO权重不一致')
        names=actual
    elif config['kind']=='dinov3_vits16_ltdetr':
        import lightly_train
        model=lightly_train.load_model(weight,device=device)
        if model.init_args.get('model_name') not in ('dinov3/vits16-ltdetr','dinov3/vits16-ltdetr-coco'):
            raise ValueError('DINOv3检测权重型号不匹配')
        actual=[model.classes[index] for index in sorted(model.classes)]
        if names and names!=actual:raise ValueError('类别顺序与DINOv3权重不一致')
        names=actual
    else:
        from rfdetr import RFDETR
        model=RFDETR.from_checkpoint(weight,device=device,
            accept_platform_model_license=config['accept_platform_model_license']) if config['kind']=='rfdetr_2xlarge' else RFDETR.from_checkpoint(weight,device=device)
        expected={'rfdetr_medium':'RFDETRMedium','rfdetr_2xlarge':'RFDETR2XLarge'}[config['kind']]
        # RF-DETR checkpoints may retain a padded detection head; never resize trained weights.
        if type(model).__name__!=expected or model.model_config.num_classes<len(names):raise ValueError('RF-DETR型号或训练类别数不匹配')
    if sha(weight)!=config['weight_sha256']:raise ValueError('加载期间权重发生变化')
    (root/'results').mkdir(exist_ok=True)
    for sample in manifest:
        result={}
        try:
            path=root/'input'/sample['file']
            if sha(path)!=sample['sha256']:raise ValueError('图片哈希不一致')
            from PIL import Image
            with Image.open(path) as image:
                if list(image.size)!=[sample['width'],sample['height']]:raise ValueError('图片尺寸不一致')
                image=image.convert('RGB')
                if config['kind']=='yolo26':
                    output=model.predict(image,device=device,conf=policy['inference_confidence'],iou=policy['nms_iou'],
                        max_det=5000,verbose=False,save=False)[0].boxes
                    coords=output.xyxy.cpu().tolist();scores=output.conf.cpu().tolist();classes=output.cls.cpu().tolist()
                elif config['kind']=='dinov3_vits16_ltdetr':
                    output=model.predict(image,threshold=policy['inference_confidence'])
                    coords=output['bboxes'].cpu().tolist();scores=output['scores'].cpu().tolist();classes=output['labels'].cpu().tolist()
                else:
                    output=model.predict(image,threshold=policy['inference_confidence'])
                    coords=output.xyxy.tolist();scores=output.confidence.tolist();classes=output.class_id.tolist()
            boxes=[]
            for bounds,score,index in zip(coords,scores,classes):
                index=int(index)
                if config['kind']=='dinov3_vits16_ltdetr':
                    if index not in model.classes:raise ValueError('预测类别编号越界')
                    label=model.classes[index]
                else:
                    if not 0<=index<len(names):raise ValueError('预测类别编号越界')
                    label=names[index]
                if policy['labels'] and label not in policy['labels']:continue
                x1,y1,x2,y2=[max(0,min(limit,value)) for value,limit in zip(bounds,(sample['width'],sample['height'],sample['width'],sample['height']))]
                if x2<=x1 or y2<=y1:continue
                boxes.append(dict(label=policy['label_mapping'].get(label,label),x=x1,y=y1,width=x2-x1,height=y2-y1,score=score))
            result={'boxes':boxes}
        except Exception as error:
            result={'error':'权重预测失败：'+type(error).__name__}
        target=root/'results'/(sample['asset_id']+'.json');temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False));os.replace(temp,target)


if __name__=='__main__':
    try:predict_batch()
    except Exception:
        traceback.print_exc();raise
