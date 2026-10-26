import { useEffect,useState } from 'react';
import { Alert,Button,Spin,Table } from 'antd';
import { getPreviewImage } from '../api/modelPreview';
import type { ValidationItem } from '../api/validation';

export default function PreviewDetectionImage({runId,item,showBoxes,hasConfidence=true}:{runId:string;item:ValidationItem;showBoxes:boolean;hasConfidence?:boolean}){
  const [url,setUrl]=useState(''),[error,setError]=useState(''),[revision,setRevision]=useState(0);
  const textSize=Math.max(1,item.width/55);
  useEffect(()=>{
    const controller=new AbortController();let objectUrl='';
    getPreviewImage(runId,item.id,controller.signal).then(blob=>{
      if(controller.signal.aborted)return;objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);
    }).catch(()=>{if(!controller.signal.aborted)setError('预览图片读取失败，请重试');});
    return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[runId,item.id,revision]);
  return <>
    <div className="model-preview-image" style={{aspectRatio:`${item.width} / ${item.height}`}}>
    {error?<Alert type="error" title={error} action={<Button onClick={()=>{setError('');setUrl('');setRevision(n=>n+1);}}>重试图片</Button>}/>:url?<>
      <img src={url} alt={item.original_name} onError={()=>setError('图片无法显示，请重试')}/>
      {showBoxes&&item.status==='completed'&&<svg role="img" aria-label="模型检测结果叠框" viewBox={`0 0 ${item.width} ${item.height}`}>
        {item.predictions.map((box,index)=><g key={index}>
          <rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke="var(--ui-primary)" strokeWidth={2} vectorEffect="non-scaling-stroke"/>
          <text x={Math.max(0,Math.min(box.x,item.width-textSize*(box.label.length+8)*.65))} y={Math.max(textSize,box.y-textSize*.3)} fontSize={textSize} fill="white" stroke="#0b101b" strokeWidth={textSize*.18} paintOrder="stroke">{box.label}{hasConfidence&&typeof box.score==='number'?` ${(box.score*100).toFixed(1)}%`:''}</text>
        </g>)}
      </svg>}
    </>:<div className="model-preview-loading"><Spin description="读取图片…"><div style={{height:48,width:160}}/></Spin></div>}
    </div>
    {item.error&&<Alert type="error" title={item.error}/>}
    {item.status==='completed'&&(!item.predictions.length?<Alert type="info" title="未检测到目标"/>:<Table rowKey={(_,i)=>String(i)} size="small" dataSource={item.predictions} pagination={{pageSize:5}} scroll={{x:360}} columns={[
      {title:'类别',dataIndex:'label'},{title:'置信度',dataIndex:'score',render:(value?:number)=>hasConfidence&&typeof value==='number'?`${(value*100).toFixed(1)}%`:'未提供'},
      {title:'位置 (x, y, 宽, 高)',render:(_,box)=>[box.x,box.y,box.width,box.height].map(n=>Math.round(n)).join(', ')},
    ]}/>)}
  </>;
}
