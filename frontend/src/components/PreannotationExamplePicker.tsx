import {useEffect,useState} from 'react';
import {Link} from 'react-router-dom';
import {Alert,Button,Input,Modal,Space,Table,Tag,Typography} from 'antd';
import {isAxiosError} from 'axios';
import {getPreannotationExamples,type PreannotationExample} from '../api/objectAttributes';
import {useAssetImageUrl} from './AssetImage';
import DetectionBoxOverlay from './DetectionBoxOverlay';

function ExampleImage({example,width}:{example:PreannotationExample;width:number|string}){
  const w=example.width||640,h=example.height||480;
  const {source,failed}=useAssetImageUrl({assetId:example.asset_id});
  return <div style={{position:'relative',width,aspectRatio:`${w}/${h}`,maxWidth:'100%'}}>
    {failed?<span>图片加载失败</span>:source&&<DetectionBoxOverlay src={source} width={w} height={h} boxes={example.boxes||[]} showScore={false} alt={example.original_name} svgLabel="人工正确范例检测框"/>}
  </div>;
}

export default function PreannotationExamplePicker({projectId,value,disabled,onChange}:{projectId:string;value?:PreannotationExample;disabled:boolean;onChange:(example:PreannotationExample|undefined)=>void}){
  const [open,setOpen]=useState(false),[page,setPage]=useState(1),[q,setQ]=useState(''),[revision,setRevision]=useState(0),[error,setError]=useState('');
  const [result,setResult]=useState<{key:string;items:PreannotationExample[];total:number}>(),[preview,setPreview]=useState<PreannotationExample>();
  const key=JSON.stringify([projectId,page,q,revision]);
  useEffect(()=>{if(!open)return;let active=true;
    getPreannotationExamples(projectId,page,q).then(data=>{if(active)setResult({...data,key});}).catch(error=>{if(active){setResult({key,items:[],total:0});setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'读取正确范例失败，请重试');}});
    return()=>{active=false;};
  },[projectId,page,q,key,open]);
  const current=result?.key===key?result:undefined;
  return <>
    <Space wrap style={{marginBottom:12}}>
      <Button disabled={disabled} onClick={()=>{setOpen(true);setError('');setRevision(n=>n+1);}}>选择正确范例（可选）</Button>
      {value?<><Tag color="blue">{value.original_name} · 人工标注 v{value.annotation_version}</Tag><Button onClick={()=>setPreview(value)}>查看范例</Button><Button disabled={disabled} onClick={()=>onChange(undefined)}>清除范例</Button></>:<Typography.Text type="secondary">可先手工标注一张图片，作为本批大模型预标注的正确示例。</Typography.Text>}
    </Space>
    <Modal title="选择人工正确范例" open={open} width={900} footer={null} onCancel={()=>setOpen(false)}>
      <p>先在项目中手工标注并保存一张图片，再刷新此列表。点击“使用此范例”表示确认标注正确；大模型会同时收到范例图片和正确的检测框 / 属性。范例不进入本批待处理图片，模型须独立判断其他图片，不能照抄范例。</p>
      <Space wrap style={{marginBottom:16}}><Input.Search aria-label="搜索正确范例" placeholder="按图片名称搜索" onSearch={value=>{setQ(value);setPage(1);}}/>
        <Button onClick={()=>{setError('');setRevision(n=>n+1);}}>刷新人工范例</Button><Link to={`/projects/${projectId}/images`} target="_blank" rel="noopener noreferrer">打开项目图片并手工标注</Link></Space>
      {error&&<Alert type="error" title={error} style={{marginBottom:12}}/>}
      <Table<PreannotationExample> rowKey="asset_id" dataSource={current?.items||[]} loading={!current} scroll={{x:650}} pagination={{current:page,total:current?.total||0,pageSize:10,showSizeChanger:false,onChange:setPage}} columns={[
        {title:'范例图片',width:140,render:(_,row)=><ExampleImage example={row} width={120}/>},
        {title:'人工标注',render:(_,row)=><Space orientation="vertical"><Typography.Text>{row.original_name} · v{row.annotation_version}</Typography.Text><Space wrap>{row.boxes?row.boxes.length?row.boxes.map((box,index)=><Tag key={index}>{box.label} · {[box.x,box.y,box.width,box.height].map(Math.round).join(', ')}</Tag>):<Tag>人工零框范例</Tag>:row.attribute_schema?.fields.map(field=><Tag key={field.key} title={row.attributes?.[field.key]?.reason}>{field.name}：{row.attributes?.[field.key]?.value??'无法判断'}</Tag>)}</Space>{!row.compatible&&<Typography.Text type="warning">标注配置不同或不完整，请按当前配置重新标注。</Typography.Text>}</Space>},
        {title:'操作',width:160,render:(_,row)=><Space orientation="vertical"><Button type="primary" disabled={!row.compatible||disabled} onClick={()=>{onChange(row);setOpen(false);}}>使用此范例</Button><Button onClick={()=>setPreview(row)}>预览标注</Button><Link to={`/annotate/${row.asset_id}${row.task_type==='detection'?'':'?from=attributes'}`} target="_blank" rel="noopener noreferrer">手工标注 / 修改</Link></Space>},
      ]}/>
    </Modal>
    <Modal title="正确范例标注预览" open={!!preview} width={850} onCancel={()=>setPreview(undefined)} footer={<Button onClick={()=>setPreview(undefined)}>关闭标注预览</Button>}>{preview&&<Space orientation="vertical" style={{width:'100%'}}><Typography.Text>{preview.original_name} · 人工标注 v{preview.annotation_version}</Typography.Text><ExampleImage example={preview} width="100%"/><Space wrap>{preview.attribute_schema?.fields.map(field=><Tag key={field.key}>{field.name}：{preview.attributes?.[field.key]?.value??'无法判断'}</Tag>)}</Space></Space>}</Modal>
  </>;
}
