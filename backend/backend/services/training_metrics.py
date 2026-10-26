"""Read-only, stdlib remote metric reader. Never imports or loads model weights."""
import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

NAMES={'results.csv','metrics.csv','metrics.json','metrics.jsonl','log.json','log.jsonl','log.txt',
       'logging.jsonl','trainer_state.json','all_results.json','train_results.json','eval_results.json'}
COCO=['AP50:95','AP50','AP75','AP_small','AP_medium','AP_large','AR1','AR10','AR100','AR_small','AR_medium','AR_large']


def number(value):
    if isinstance(value,bool) or value is None or value=='':return None
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError,OverflowError):return None


def direction(name):
    name=name.lower()
    if any(word in name for word in ('loss','error','mean_neg')):return 'lower'
    if any(word in name for word in ('map','/ap','coco_eval','precision','recall','f1','accuracy','acc','pearson','spearman','mean_pos','margin')):return 'higher'
    return None


def flatten(row,prefix=''):
    for key,value in row.items():
        name=prefix+str(key).strip()
        if isinstance(value,dict):yield from flatten(value,name+'/')
        elif isinstance(value,list) and 'coco_eval_bbox' in name:
            for label,item in zip(COCO,value):yield name+'/'+label,item
        elif not isinstance(value,(list,dict)):yield name,value


def parse_rows(path,text):
    if path.suffix=='.csv':return list(csv.DictReader(io.StringIO(text)))
    try:
        data=json.loads(text)
        if isinstance(data,dict):return data.get('log_history',[data])
        if isinstance(data,list):return data
        return []
    except ValueError:
        rows=[]
        for line in text.splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if isinstance(row,dict):rows.append(row)
        return rows


def collect_metrics(root):
    root=Path(root).absolute();warnings=[];files=[];metrics=[];candidates=[]
    if root.resolve()!=root or not root.is_dir():
        return dict(files=[],metrics=[],warnings=['训练产物目录不存在或包含符号链接，无法读取指标。'])
    visited=0
    for parent,dirs,names in os.walk(root,followlinks=False):
        visited+=1
        dirs[:]=sorted(d for d in dirs if not (Path(parent)/d).is_symlink() and len((Path(parent)/d).relative_to(root).parts)<6)
        if visited>200:
            warnings.append('目录扫描已达200个上限，部分指标可能未读取。');break
        for name in sorted(names):
            if name not in NAMES:continue
            path=Path(parent)/name
            if path.is_symlink() or not path.is_file():continue
            candidates.append(path)
    # Swift checkpoints repeat cumulative history; select one state, not all copies.
    states=[p for p in candidates if p.name=='trainer_state.json']
    if states:
        latest=max(states,key=lambda p:(p.stat().st_mtime_ns,str(p)))
        candidates=[p for p in candidates if p.name!='trainer_state.json' or p==latest]
    total=0
    for path in sorted(candidates,key=lambda p:(p.name not in ('metrics.csv','results.csv','trainer_state.json','logging.jsonl'),str(p))):
        name=str(path.relative_to(root));before=path.stat()
        if before.st_size>4*1024*1024 or total+before.st_size>8*1024*1024 or len(files)>=12:
            warnings.append(name+'：超过读取大小/文件数量限制，未读取。');continue
        try:
            # Bounded read even if a file grows between stat and read.
            with path.open('rb') as stream:raw=stream.read(4*1024*1024+1)
            after=path.stat()
            if path.resolve()!=path or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or len(raw)!=before.st_size:
                warnings.append(name+'：读取期间发生变化，已跳过，请在训练结束后重试。');continue
            total+=len(raw)
            rows=parse_rows(path,raw.decode('utf-8-sig',errors='replace'))
            if len(rows)>50000:warnings.append(name+'：仅分析前50000条记录。');rows=rows[:50000]
            values={};invalid=0
            for index,row in enumerate(rows):
                if not isinstance(row,dict):continue
                row={str(k).strip():v for k,v in row.items() if k is not None}
                step=number(row.get('step',row.get('global_step')));epoch=number(row.get('epoch'))
                for key,value in flatten(row):
                    if key in ('epoch','step','global_step') or len(key)>160:continue
                    numeric=number(value)
                    if numeric is None:
                        if str(value).lower() in ('nan','inf','-inf','infinity'):invalid+=1
                        continue
                    values.setdefault(key,[]).append(dict(value=numeric,step=step,epoch=epoch,index=index))
            if invalid:warnings.append(name+'：已忽略NaN/Infinity等非有限数值。')
            files.append(dict(path=name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),rows=len(rows)))
            for key,points in sorted(values.items(),key=lambda kv:('val' not in kv[0].lower() and 'eval' not in kv[0].lower(),kv[0])):
                if len(metrics)>=80:continue
                order=direction(key)
                best=(min if order=='lower' else max)(points,key=lambda p:p['value']) if order else None
                indices=sorted({round(i*(len(points)-1)/min(79,len(points)-1)) for i in range(min(80,len(points)))}) if len(points)>1 else [0]
                metrics.append(dict(name=key,file=name,count=len(points),first=points[0]['value'],last=points[-1]['value'],
                    last_step=points[-1]['step'],last_epoch=points[-1]['epoch'],direction=order,
                    best=best['value'] if best else None,best_epoch=best['epoch'] if best else None,best_step=best['step'] if best else None,
                    series=[points[i] for i in indices]))
            if sum(len(flattened) for flattened in values.values()) and len(metrics)>=80:
                warnings.append('最多展示80项指标，优先保留验证指标；完整记录请下载原始指标文件。')
        except (OSError,ValueError,csv.Error,TypeError):warnings.append(name+'：指标文件无法解析或读取。')
    if not metrics:warnings.append('未找到可解析的指标。支持CSV、Swift trainer_state/logging JSONL、RF-DETR COCO日志；不从权重或控制台文本猜测数值。')
    recorded_labels=[]
    config=root/'training_config.json'
    if config.is_file() and not config.is_symlink() and config.stat().st_size<=65536:
        try:
            raw=config.read_bytes()[:65537]
            data=json.loads(raw)
            labels=data.get('class_names')
            if isinstance(labels,list) and 0<len(labels)<=100 and all(isinstance(v,str) and 0<len(v)<=100 for v in labels):
                recorded_labels=labels
                files.append(dict(path='training_config.json',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),rows=0))
        except (OSError,ValueError,TypeError):warnings.append('训练类别元数据无法读取，需人工核对类别顺序。')
    return dict(files=files,metrics=metrics,warnings=list(dict.fromkeys(warnings)),recorded_labels=recorded_labels)


if __name__=='__main__':
    try:
        payload=json.loads(base64.b64decode(sys.argv[1]))
        from uuid import UUID
        config=payload['config'];run_id=str(UUID(payload['run_id']))
        root=Path(config['output_root'])/run_id/config['output_subdir']
        print(json.dumps(dict(success=True,result=collect_metrics(root)),ensure_ascii=False,allow_nan=False))
    except Exception:
        print(json.dumps(dict(success=False,error='训练指标读取失败，请检查产物目录和服务器连接')))
        sys.exit(1)
