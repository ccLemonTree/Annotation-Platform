import { useEffect,useState } from 'react';
import { Alert,Button,Modal,Space,Spin,Typography } from 'antd';
import apiClient from '../api/client';
import type { MatchHit,SmartSearchReference } from '../api/projectWork';
import DetectionCanvas from './DetectionCanvas';
import type { Box } from './detectionEditor';

export default function SmartSearchCrop({projectId,queryId,hit,onCancel,onSearch}:{projectId:string;queryId:string;hit:MatchHit;onCancel:()=>void;onSearch:(reference:SmartSearchReference)=>void}){
  const meta=hit.smart_search!;
  const initial:Box={x:meta.region[0],y:meta.region[1],width:meta.region[2]-meta.region[0],height:meta.region[3]-meta.region[1],label:'查询目标'};
  const [image,setImage]=useState(''),[error,setError]=useState(''),[boxes,setBoxes]=useState<Box[]>([initial]),[selected,setSelected]=useState(0),[dragging,setDragging]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();let objectUrl:string|undefined;
    apiClient.get(`/api/projects/${projectId}/matches/${queryId}/source/${hit.asset_id}`,{responseType:'blob',signal:controller.signal})
      .then(response=>{if(!controller.signal.aborted){objectUrl=URL.createObjectURL(response.data);setImage(objectUrl);}})
      .catch(()=>{if(!controller.signal.aborted)setError('原图读取失败，请关闭后重试');});
    return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[projectId,queryId,hit.asset_id]);
  const box=boxes[0];
  return <Modal title="框选目标，以图搜图" open width={960} onCancel={onCancel} footer={<Space><Button onClick={onCancel}>取消</Button><Button type="primary" disabled={!image||!!error||!box||dragging} onClick={()=>{if(box)onSearch({reference_query_id:queryId,reference_asset_id:hit.asset_id,region:{x:box.x,y:box.y,width:box.width,height:box.height}});}}>使用此框搜图</Button></Space>}>
    <Typography.Paragraph>默认使用当前目标框，可拖动、调整大小；点击“重新框选”后在原图上拖出一个框。</Typography.Paragraph>
    <Space style={{marginBottom:12}}><Button disabled={!image||dragging} onClick={()=>{setBoxes([]);setSelected(-1);}}>重新框选</Button><Button disabled={dragging} onClick={()=>{setBoxes([initial]);setSelected(0);}}>恢复原框</Button></Space>
    {error?<Alert type="error" title={error}/>:!image?<Spin/>:<DetectionCanvas image={image} width={meta.original_width} height={meta.original_height} boxes={boxes} selected={selected} label="查询目标" tool="draw" disabled={false} onSelect={setSelected} onDragging={setDragging} onChange={rows=>{setBoxes(rows.slice(-1));setSelected(rows.length?0:-1);}}/>}
  </Modal>;
}
