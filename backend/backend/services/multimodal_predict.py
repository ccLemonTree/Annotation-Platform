"""Offline Swift inference: load one merged model or base+LoRA for the whole batch."""
import hashlib
import json
import math
from pathlib import Path
import traceback


def load_engine(config):
    import torch
    try:from swift.infer_engine import TransformersEngine,InferRequest,RequestConfig
    except ImportError:from swift.llm import PtEngine as TransformersEngine,InferRequest,RequestConfig
    kwargs=dict(task_type='embedding' if config['kind']=='embedding' else 'causal_lm',max_batch_size=1,
        torch_dtype=torch.float32 if config['device']=='cpu' else torch.float16,device_map='cpu' if config['device']=='cpu' else 'cuda:0')
    if config.get('model_type'):kwargs['model_type']=config['model_type']
    if config.get('base_model_path'):kwargs['adapters']=['/weights/model']
    engine=TransformersEngine('/weights/base' if config.get('base_model_path') else '/weights/model',**kwargs)
    policy=config['policy'];request_config=RequestConfig(max_tokens=policy.get('max_tokens',512),temperature=0)
    def infer(path=None,text=None):
        prompt=text or (policy.get('prompt') or '描述图片中可见的内容，不要猜测。' if config['kind']=='caption' else '表示这张图片的可见内容。')
        request=InferRequest(messages=[dict(role='user',content=('<image>' if path else '')+prompt)],images=[str(path)] if path else [])
        result=engine.infer([request],request_config)[0]
        return result.data[0].embedding if config['kind']=='embedding' else result.choices[0].message.content
    return infer


def predict_batch(root='/job',engine_factory=load_engine):
    from PIL import Image
    root=Path(root);config=json.loads((root/'config.json').read_text());samples=json.loads((root/'input/manifest.json').read_text())
    infer=engine_factory(config)
    output=root/'results';output.mkdir(exist_ok=True)
    for sample in samples:
        if (root/'.canceled').exists():break
        try:
            path=root/'input'/sample['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sample['sha256']:raise ValueError('原图哈希不一致')
            with Image.open(path) as picture:
                if picture.size!=(sample['width'],sample['height']):raise ValueError('原图尺寸不一致')
                picture=picture.convert('RGB');picture.load();picture.thumbnail((1024,1024))
                prepared=root/'input/.inference.jpg';picture.save(prepared,format='JPEG',quality=90)
            value=infer(path=prepared)
            if config['kind']=='caption':
                if not isinstance(value,str) or not value.strip() or len(value)>20000:raise ValueError('描述输出无效')
                result=dict(caption=value)
            else:
                result=dict(vector=value,queries={id:infer(text=text) for id,text in sample.get('query_texts',{}).items()})
                for values in [value,*result['queries'].values()]:
                    if not isinstance(values,list) or not 0<len(values)<=16384 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values) or not math.hypot(*values):raise ValueError('向量无效')
            (output/(sample['asset_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
        except Exception:
            traceback.print_exc()
            (output/(sample['asset_id']+'.json')).write_text(json.dumps(dict(error='此图模型推理失败，请检查图片及容器日志')))


if __name__=='__main__':predict_batch()
