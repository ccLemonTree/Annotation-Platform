import {useEffect,useState} from 'react';
import {Alert,Button,Input,Modal,Space,Table} from 'antd';
import {getAssets,type Asset} from '../api/assets';
import AssetImage from './AssetImage';

export type ProjectTestImage=Pick<Asset,'id'|'original_name'>;
export default function ProjectTestImages({projectId,value,onChange,disabled}:{projectId:string;value:ProjectTestImage[];onChange:(images:ProjectTestImage[])=>void;disabled:boolean}){
  const [open,setOpen]=useState(false),[page,setPage]=useState(1),[q,setQ]=useState(''),[revision,setRevision]=useState(0);
  const [result,setResult]=useState<{key:string;items:Asset[];total:number;error?:string}>(),[selectionError,setSelectionError]=useState('');
  const key=JSON.stringify([projectId,page,q,revision]);
  const current=result?.key===key?result:undefined;
  useEffect(()=>{if(!open)return;let active=true;
    getAssets({project_id:projectId,page,page_size:20,q:q||undefined,image_kind:'all'}).then(data=>{if(active)setResult({...data,key});})
      .catch(()=>{if(active)setResult({key,items:[],total:0,error:'项目图片读取失败，请重试'});});
    return()=>{active=false;};
  },[projectId,page,q,key,open]);
  return <>
    <Space wrap><Button disabled={disabled} onClick={()=>{setOpen(true);setRevision(n=>n+1);}}>选择项目图片</Button>
      {!!value.length&&<><span>已选 {value.length} 张，按下方顺序测试</span><Button disabled={disabled} onClick={()=>onChange([])}>清空项目选图</Button></>}</Space>
    {!!value.length&&<ol style={{paddingInlineStart:24,maxHeight:200,overflowY:'auto',overflowWrap:'anywhere'}}>{value.map((image,index)=><li key={image.id}>{image.original_name}<Button type="text" size="small" disabled={disabled} aria-label={`移除项目测试图片 ${index+1}`} onClick={()=>onChange(value.filter(row=>row.id!==image.id))}>移除</Button></li>)}</ol>}
    <Modal title="选择项目测试图片" open={open} width={800} onCancel={()=>setOpen(false)} footer={<Button type="primary" onClick={()=>setOpen(false)}>完成选图</Button>}>
      <p>选择当前项目已有图片，保留选择顺序，最多5000张。只用于测试，不修改图片、标注或项目引用。</p>
      <Space wrap style={{marginBottom:12}}><Input.Search aria-label="搜索项目测试图片" placeholder="按图片名称搜索" allowClear onSearch={text=>{setQ(text);setPage(1);}}/><Button onClick={()=>setRevision(n=>n+1)}>刷新项目图片</Button><span>已选 {value.length} 张</span></Space>
      {(current?.error||selectionError)&&<Alert type="error" title={current?.error||selectionError}/>}
      <Table<Asset> rowKey="id" dataSource={current?.items||[]} loading={!current} scroll={{x:500}}
        rowSelection={{selectedRowKeys:value.map(image=>image.id),preserveSelectedRowKeys:true,onChange:keys=>{
          if(keys.length>5000){setSelectionError('每次最多选择5000张项目图片');return;}
          setSelectionError('');const byId=new Map([...value,...(current?.items||[])].map(image=>[image.id,image]));
          onChange(keys.map(id=>byId.get(String(id))).filter((image):image is ProjectTestImage=>!!image));
        }}}
        columns={[{title:'图片',width:90,render:(_,image)=><AssetImage assetId={image.id} alt={image.original_name} width={56} height={56} preview={false} style={{objectFit:'contain'}}/>},{title:'文件名',dataIndex:'original_name'},{title:'选择顺序',width:100,render:(_,image)=>{const index=value.findIndex(row=>row.id===image.id);return index<0?'—':index+1;}}]}
        pagination={{current:page,pageSize:20,total:current?.total||0,showSizeChanger:false,onChange:setPage}}/>
    </Modal>
  </>;
}
