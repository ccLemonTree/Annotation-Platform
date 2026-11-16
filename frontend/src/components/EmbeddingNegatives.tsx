import {useEffect,useState} from 'react';
import {Alert,Button,Input,Modal,Space,Table} from 'antd';
import apiClient from '../api/client';
import {getAssets,type Asset} from '../api/assets';
import type {ExportScope} from './ProjectExport';
import AssetImage from './AssetImage';

type Selection={text:string;version:number;asset_ids:string[]};
export default function EmbeddingNegatives({projectId,source,scope,onClose,onSaved}:{
  projectId:string;source:string;scope:ExportScope;onClose:()=>void;onSaved:()=>void;
}){
  const [anchor,setAnchor]=useState<Asset>();
  const [selection,setSelection]=useState<Selection>();
  const [page,setPage]=useState(1),[search,setSearch]=useState('');
  const [rows,setRows]=useState<Asset[]>([]),[total,setTotal]=useState(0),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false);
  const {work_job_id,image_kind}=scope;
  useEffect(()=>{
    let active=true;
    setLoading(true);setRows([]);
    getAssets({project_id:projectId,scope:'annotation',page,page_size:12,q:search||undefined,
      ...(anchor?{}:{work_job_id,image_kind})})
      .then(result=>{if(active){setRows(result.items);setTotal(result.total);}})
      .catch(()=>{}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[anchor,projectId,page,search,work_job_id,image_kind]);
  const url=(id:string)=>`/api/export/projects/${projectId}/embedding-negatives/${id}`;
  const choose=async(row:Asset)=>{
    setBusy(true);
    try{
      const {data}=await apiClient.get<Selection>(url(row.id),{params:{source}});
      setAnchor(row);setSelection(data);setPage(1);setSearch('');
    }catch{/* The API client displays the error. */}
    finally{setBusy(false);}
  };
  const save=async()=>{
    if(!anchor||!selection||!selection.text)return;
    setBusy(true);
    try{
      const {data}=await apiClient.put<Selection>(url(anchor.id),selection,{params:{source}});
      setSelection(data);onSaved();onClose();
    }catch{/* The API client displays the error. */}
    finally{setBusy(false);}
  };
  return <Modal title="人工确认 InfoNCE 负样本" open width={760} onCancel={()=>!busy&&onClose()}
    footer={<Space><Button disabled={busy} onClick={onClose}>关闭</Button>
      <Button type="primary" loading={busy} disabled={!selection?.text} onClick={()=>void save()}>保存负样本</Button></Space>}>
    {anchor?<Space orientation="vertical" style={{width:'100%',marginBottom:12}}>
      <Button disabled={busy} onClick={()=>{setAnchor(undefined);setSelection(undefined);setSearch('');setPage(1);}}>更换正样本图片</Button>
      <Alert type={selection?.text?'info':'warning'} title={selection?.text||'当前图片没有所选类型的有效标注'}
        description={`正样本 ${anchor.original_name}。勾选确定与上方文字不匹配的图片，最多 20 张。`}/>
    </Space>:<p>先选择描述或属性对应的正样本图片，再确认与该文字不匹配的负图。</p>}
    <Input.Search key={anchor?.id||'anchor'} aria-label="搜索配对图片" placeholder="按图片文件名搜索" allowClear disabled={busy}
      onSearch={value=>{setSearch(value.trim());setPage(1);}} style={{marginBottom:12}}/>
    <Table rowKey="id" size="small" loading={loading} dataSource={rows}
      pagination={{current:page,pageSize:12,total,onChange:setPage,showSizeChanger:false}}
      rowSelection={anchor&&selection?.text?{selectedRowKeys:selection.asset_ids,preserveSelectedRowKeys:true,hideSelectAll:true,
        onChange:keys=>setSelection({...selection,asset_ids:keys.map(String)}),
        getCheckboxProps:row=>({disabled:busy||row.id===anchor.id||row.content_hash===anchor.content_hash||
          selection.asset_ids.length>=20&&!selection.asset_ids.includes(row.id)})}:undefined}
      columns={[
        {title:'预览',width:88,render:(_,row)=><AssetImage assetId={row.id} width={64} height={48} alt={row.original_name} style={{objectFit:'contain'}}/>},
        {title:'图片',dataIndex:'original_name',ellipsis:true},
        ...(!anchor?[{title:'操作',width:110,render:(_:unknown,row:Asset)=><Button disabled={busy} onClick={()=>void choose(row)}>选择正样本</Button>}]:[]),
      ]}/>
    {anchor&&selection&&<Space wrap><span>已选 {selection.asset_ids.length} 张负样本</span>
      <Button type="link" disabled={busy||!selection.asset_ids.length} onClick={()=>setSelection({...selection,asset_ids:[]})}>清空已选</Button></Space>}
  </Modal>;
}
