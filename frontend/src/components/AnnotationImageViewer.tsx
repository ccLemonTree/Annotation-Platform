import {useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import {Alert,Button,Radio,Select,Spin} from 'antd';
import {isAxiosError} from 'axios';
import apiClient from '../api/client';
import type {Asset,AssetImageKind} from '../api/assets';
import DetectionCanvas from './DetectionCanvas';
import useAnnotationImage from './useAnnotationImage';
import type {ImageView} from './useAnnotationImageView';
import type {Box} from './detectionEditor';

type Source={original_name:string;width:number;height:number;region:number[]|null;content_url:string};
function ImageCanvas({url,width,height,boxes=[],name,toolbarExtra}:{url:string;width:number;height:number;boxes?:Box[];name:string;toolbarExtra?:ReactNode}){
  const image=useAnnotationImage(url);
  if(!image.src)return <>{toolbarExtra}{image.previewError?<ImagePreviewError image={image}/>:<Spin/>}</>;
  return <DetectionCanvas key={url} image={image.src} width={width} height={height} boxes={boxes} selected={-1} label="" tool="select" disabled ariaLabel={name} toolbarExtra={<>{toolbarExtra}<FullImageStatus image={image}/></>} onZoomIn={image.loadFull} onSelect={()=>{}} onChange={()=>{}} onDragging={()=>{}}/>;
}

export function ImagePreviewError({image}:{image:ReturnType<typeof useAnnotationImage>}){
  return <Alert type="error" title={image.fullError?'图片加载失败':'预览加载失败'}
    action={<Button loading={image.fullLoading} onClick={image.loadFull}>{image.fullError?'重试原尺寸':'加载原尺寸'}</Button>}/>;
}

export function FullImageStatus({image}:{image:ReturnType<typeof useAnnotationImage>}){
  if(image.fullLoading)return <span role="status">正在加载原尺寸…</span>;
  if(image.fullError)return <Button onClick={image.loadFull}>重试原尺寸</Button>;
  return null;
}

export function AnnotationSourceViewer({asset,toolbarExtra}:{asset:Asset;toolbarExtra?:ReactNode}){
  const [source,setSource]=useState<Source>(),[error,setError]=useState(''),[reload,setReload]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();setSource(undefined);setError('');
    apiClient.get<Source>(`/api/assets/${asset.id}/source`,{signal:controller.signal}).then(response=>setSource(response.data))
      .catch(error=>{if(!controller.signal.aborted)setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'原图读取失败，请重试');});
    return()=>controller.abort();
  },[asset.id,reload]);
  const region=source?.region;
  const boxes=region?[{x:region[0],y:region[1],width:region[2]-region[0],height:region[3]-region[1],label:'当前裁剪区域'}]:[];
  if(error||!source)return <>{toolbarExtra}{error?<Alert type="error" title={error} action={<Button onClick={()=>setReload(v=>v+1)}>重试原图</Button>}/>:<Spin/>}</>;
  return <ImageCanvas url={source.content_url} width={source.width} height={source.height} boxes={boxes} name="原图裁剪位置" toolbarExtra={toolbarExtra}/>;
}

export function AnnotationCropLabelSelect({labels,value,disabled,onChange}:{
  labels:string[];value:string[];disabled:boolean;onChange:(value:string[])=>void;
}){
  if(!labels.length&&!value.length)return null;
  return <Select mode="multiple" aria-label="筛选切图标签" prefix="切图标签：" placeholder="全部" allowClear showSearch optionFilterProp="label"
    value={value} options={labels.map(label=>({value:label,label}))} maxTagCount={2} style={{minWidth:185,maxWidth:360}} disabled={disabled} onChange={onChange}/>;
}

export function AnnotationImageKindSelect({value,cropsOnly,disabled,onChange}:{
  value:AssetImageKind;cropsOnly:boolean;disabled:boolean;onChange:(value:AssetImageKind)=>void;
}) {
  return <Select aria-label="筛选图片来源" value={value} disabled={disabled} onChange={onChange} style={{minWidth:125}}
    options={[{value:'all',label:cropsOnly?'全部裁剪图':'全部图片'},{value:'originals',label:'仅原图',disabled:cropsOnly},{value:'crops',label:'仅裁剪图'}]}/>;
}

export default function AnnotationImageViewer({asset,view,setView}:{asset:Asset;view:ImageView;setView:(view:ImageView)=>void}){
  const isCrop=asset.source_type==='model_crop'&&!asset.device_info?.uploaded_crop;
  const switcher=isCrop&&<Radio.Group aria-label="图片视图" data-annotation-view value={view} onChange={event=>setView(event.target.value)}>
      <Radio.Button value="crop">标注切图</Radio.Button><Radio.Button value="source">来源原图</Radio.Button>
    </Radio.Group>;
  return <div className="annotation-image-viewer">
    {isCrop&&view==='source'?<AnnotationSourceViewer asset={asset} toolbarExtra={switcher}/>:
      <ImageCanvas url={`/api/assets/${asset.id}/content`} width={asset.width||1} height={asset.height||1} name="当前标注图片缩放区域" toolbarExtra={switcher}/>}
  </div>;
}
