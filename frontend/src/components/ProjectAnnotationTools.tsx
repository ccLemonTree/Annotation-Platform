import { useEffect,useRef,useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert,Button,Checkbox,Input,InputNumber,Pagination,Select,Space,Table,Tabs,Tag,Typography } from 'antd';
import { isAxiosError } from 'axios';
import { getAssets,getAllAssetIds,type Asset } from '../api/assets';
import { getModelServices,type ModelService } from '../api/modelServices';
import type { Project } from '../api/projects';
import { createWork,getWork,getWorkJobs,workAction,type Operation,type WorkJob,type WorkDetail } from '../api/projectWork';
import AssetImage from './AssetImage';
import ProjectAnnotationScope from './ProjectAnnotationScope';
import DeleteCropButton from './DeleteCropButton';
import DeleteSelectedImages from './DeleteSelectedImages';

const operations={crop:'检测切图',caption:'生成描述',index:'建立向量索引'};
const workLabels={...operations,match_review:'匹配大模型审核'};
const statuses:Record<string,string>={pending:'等待',running:'处理中',completed:'完成',failed:'失败',cancelled:'已取消'};
export default function ProjectAnnotationTools({project,onRefresh}:{project:Project;onRefresh:()=>void}){
  const [models,setModels]=useState<ModelService[]>([]),[model,setModel]=useState<string>();
  const [operation,setOperation]=useState<Operation>(['vector','caption'].includes(project.task_type)?'caption':'crop');
  const [cropsOnly,setCropsOnly]=useState(true),[imageKind,setImageKind]=useState<'all'|'originals'|'crops'>('all');
  const [assets,setAssets]=useState<Asset[]>([]),[total,setTotal]=useState(0),[page,setPage]=useState(1),[selected,setSelected]=useState<React.Key[]>([]);
  const [prompt,setPrompt]=useState(''),[targets,setTargets]=useState<string[]>([]),[confidence,setConfidence]=useState(.4),[expansion,setExpansion]=useState(20);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[info,setInfo]=useState(''),[reload,setReload]=useState(0);
  const [panel,setPanel]=useState('prepare');
  const [jobs,setJobs]=useState<WorkJob[]>([]),[jobPage,setJobPage]=useState(1),[jobId,setJobId]=useState<string>(),[job,setJob]=useState<WorkDetail>(),[itemPage,setItemPage]=useState(1);
  const mounted=useRef(true);
  const fail=(error:unknown)=>{if(mounted.current)setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'操作失败，请重试');};
  useEffect(()=>{mounted.current=true;getModelServices({enabled:true}).then(rows=>{if(mounted.current)setModels(rows);}).catch(fail);return()=>{mounted.current=false;};},[]);
  useEffect(()=>{let active=true;getAssets({project_id:project.id,page,page_size:20,image_kind:imageKind,scope:operation==='crop'?'all':'annotation'}).then(data=>{if(active){setAssets(data.items);setTotal(data.total);}}).catch(fail);return()=>{active=false;};},[project.id,project.label_schema?.annotation_scope,page,reload,imageKind,operation]);
  useEffect(()=>{let active=true,inFlight=false;
    const load=async()=>{if(inFlight)return;inFlight=true;try{const rows=await getWorkJobs(project.id,jobPage);if(active)setJobs(rows);}catch(error){if(active)fail(error);}finally{inFlight=false;}};
    void load();const timer=setInterval(load,3000);return()=>{active=false;clearInterval(timer);};
  },[project.id,jobPage,reload]);
  useEffect(()=>{if(!jobId)return;let active=true,inFlight=false;
    const load=async()=>{if(inFlight)return;inFlight=true;try{const detail=await getWork(project.id,jobId,itemPage);if(active)setJob(detail);}catch(error){if(active)fail(error);}finally{inFlight=false;}};
    void load();const timer=setInterval(load,3000);return()=>{active=false;clearInterval(timer);};
  },[project.id,jobId,itemPage,reload]);
  const create=async()=>{if(!model||!selected.length)return;setBusy(true);setError('');setInfo('');try{
    const created=await createWork(project.id,{operation,model_id:model,asset_ids:selected.map(String),prompt,labels:targets,confidence,expansion:expansion/100,crops_only:cropsOnly});
    if(!mounted.current)return;setJobId(created.id);setItemPage(1);setSelected([]);setJobPage(1);setPanel('jobs');setReload(v=>v+1);onRefresh();setInfo(`已排队 ${created.total} 张图片。完成后从项目导航开始标注；如需后续处理，返回“处理图片”刷新列表。`);
  }catch(error){fail(error);}finally{if(mounted.current)setBusy(false);}};
  const action=async(action:'cancel'|'retry')=>{if(!jobId)return;setBusy(true);setError('');try{await workAction(project.id,jobId,action);if(mounted.current)setReload(v=>v+1);}catch(error){fail(error);}finally{if(mounted.current)setBusy(false);}};
  const choices=models.filter(m=>m.kind===({crop:'detection',caption:'caption',index:'embedding'}[operation]));
  const detail=job?.id===jobId&&job?.page===itemPage?job:undefined;
  return <Space orientation="vertical" size="middle" style={{width:'100%',marginBottom:24}}>
    <ProjectAnnotationScope project={project} onSaved={()=>{setSelected([]);setPage(1);setReload(v=>v+1);onRefresh();}}/>
    {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>setReload(v=>v+1)}>刷新重试</Button>}/>}{info&&<Alert type="success" title={info}/>}
    <Tabs activeKey={panel} onChange={setPanel} items={[{key:'prepare',label:'处理图片 · 切图 / 描述 / 索引',children:<>
      <Typography.Paragraph type="secondary">每个检测框生成当前项目的私有裁剪图，原图保留作来源。图片描述无需向量；仅文搜图、以图搜图需要建立索引。</Typography.Paragraph>
      <Space wrap style={{marginBottom:16}}>
        <Select aria-label="处理方式" value={operation} style={{width:180}} disabled={busy} onChange={value=>{setOperation(value);setModel(undefined);setSelected([]);setPage(1);setImageKind('all');}} options={Object.entries(operations).filter(([key])=>key==='crop'||key==='caption'&&['caption','vector'].includes(project.task_type)||key==='index'&&project.task_type==='vector').map(([value,label])=>({value,label}))}/>
        <Select aria-label="处理模型" value={model} placeholder="选择模型管理中的模型" style={{width:280,maxWidth:'100%'}} disabled={busy} onChange={setModel} options={choices.map(m=>({value:m.id,label:`${m.name} · r${m.revision}`}))}/>
        <Link to={`/models?project_id=${project.id}`}>配置模型 / 提示词</Link>
        {operation==='crop'&&<><Select aria-label="裁剪目标类别" mode="tags" placeholder="目标类别；留空使用模型类别" value={targets} style={{width:260,maxWidth:'100%'}} disabled={busy} onChange={setTargets} tokenSeparators={[',','，']} options={(models.find(m=>m.id===model)?.labels||[]).map(label=>({label,value:label}))}/>
          <label>置信度 <InputNumber aria-label="裁剪置信度" value={confidence} min={0} max={1} step={.05} disabled={busy} onChange={v=>setConfidence(v??.4)}/></label>
          <label>每侧外扩 <InputNumber aria-label="裁剪每侧外扩百分比" value={expansion} min={0} max={100} suffix="%" disabled={busy} onChange={v=>setExpansion(v??20)}/></label></>}
        {operation==='crop'&&<Checkbox checked={cropsOnly} disabled={busy} onChange={e=>setCropsOnly(e.target.checked)}>切图后仅标注裁剪图</Checkbox>}
      </Space>
      {operation==='caption'&&<Input.TextArea aria-label="本批描述提示词" placeholder="留空使用模型管理中的描述提示词；可针对当前任务修改" value={prompt} disabled={busy} maxLength={2000} autoSize={{minRows:2,maxRows:8}} onChange={e=>setPrompt(e.target.value)} style={{marginBottom:16}}/>}
      <Space wrap style={{marginBottom:12}}><Select aria-label="准备区图片来源" value={imageKind} disabled={busy} style={{width:160}} onChange={value=>{setImageKind(value);setPage(1);setSelected([]);}} options={[{value:'all',label:'全部来源'},{value:'originals',label:'仅原图'},{value:'crops',label:'仅裁剪图'}]}/><Button type="primary" loading={busy} disabled={!model||!selected.length||busy} onClick={create}>{operations[operation]} · 已选 {selected.length} 张</Button><Button disabled={busy} onClick={()=>{setReload(v=>v+1);onRefresh();}}>刷新图片列表</Button></Space>
      <Space wrap style={{marginBottom:12}}><DeleteSelectedImages ids={selected.map(String)} disabled={busy} onDeleted={()=>{setSelected([]);setPage(1);setReload(v=>v+1);onRefresh();}}/>
        <DeleteSelectedImages disabled={busy||!total} allLabel={imageKind==='crops'||operation!=='crop'&&project.label_schema?.annotation_scope==='crops'?'删除全部裁剪图':'删除全部图片'} rangeLabel={`${project.project_name} · 当前准备范围 · ${{all:'全部来源',originals:'仅原图',crops:'仅裁剪图'}[imageKind]}`} loadAll={()=>getAllAssetIds({project_id:project.id,scope:operation==='crop'?'all':'annotation',image_kind:imageKind})} onDeleted={()=>{setSelected([]);setPage(1);setReload(v=>v+1);onRefresh();}}/>
      </Space>
      <Table<Asset> rowKey="id" dataSource={assets} scroll={{x:560}} rowSelection={{selectedRowKeys:selected,preserveSelectedRowKeys:true,onChange:setSelected,getCheckboxProps:()=>({disabled:busy})}} pagination={{current:page,total,pageSize:20,showSizeChanger:false,onChange:setPage}} columns={[
        {title:'图片',width:90,render:(_,asset)=><AssetImage assetId={asset.id} width={64} height={48} style={{objectFit:'contain'}}/>},
        {title:'名称',dataIndex:'original_name',ellipsis:true},{title:'来源',width:110,render:(_,asset)=><Tag>{asset.source_type==='model_crop'?'裁剪图':'原图'}</Tag>},
        {title:'操作',width:270,render:(_,asset)=><Space wrap>{project.label_schema?.annotation_scope==='crops'&&asset.source_type!=='model_crop'?<Typography.Text type="secondary">仅作切图来源</Typography.Text>:<Link to={`/annotate/${asset.id}`}>{project.task_type==='vector'?'编辑描述 / 分类':'标注 / 审核'}</Link>}{asset.source_type==='model_crop'&&<DeleteCropButton assetId={asset.id} name={asset.original_name} disabled={busy} onDeleted={()=>{setSelected(keys=>keys.filter(key=>key!==asset.id));if(assets.length===1&&page>1)setPage(page-1);setReload(v=>v+1);onRefresh();}}/>}</Space>},]}/>
    </>},{key:'jobs',label:'后台处理队列',children:<>
      <Table<WorkJob> rowKey="id" dataSource={jobs} pagination={false} scroll={{x:650}} columns={[
        {title:'批次',render:(_,row)=><Button type="link" onClick={()=>{setJobId(row.id);setItemPage(1);}}>{workLabels[row.operation]} · {row.id.slice(0,8)}</Button>},
        {title:'总数',dataIndex:'total'},...Object.entries(statuses).map(([status,title])=>({title,key:status,render:(_:unknown,row:WorkJob)=>row.counts[status]||0}))]}/>
      <Space><Button disabled={jobPage===1} onClick={()=>setJobPage(v=>v-1)}>上一批</Button><span>第 {jobPage} 页</span><Button disabled={jobs.length<20} onClick={()=>setJobPage(v=>v+1)}>下一批</Button></Space>
      {detail&&<><Typography.Title level={5}>{workLabels[detail.operation]} · {detail.id.slice(0,8)}</Typography.Title><Space wrap>
        <Button danger disabled={busy||!(detail.counts.pending||detail.counts.running)} onClick={()=>action('cancel')}>取消未完成项</Button>
        <Button disabled={busy||!(detail.counts.failed||detail.counts.cancelled)} onClick={()=>action('retry')}>重试失败与取消项</Button></Space>
        <Table rowKey="id" dataSource={detail.items} pagination={false} scroll={{x:600}} columns={[
          {title:'图片',render:(_,item)=>item.asset_id?<Link to={`/annotate/${item.asset_id}`}>{item.asset_id.slice(0,8)}</Link>:'已移除'},
          {title:'状态',render:(_,item)=>statuses[item.status]||item.status},{title:'结果',render:(_,item)=>item.error||item.result?.reason||item.result?.warning||item.result?.description||(item.result?.detected_count!==undefined?`生成 ${item.result.detected_count} 张裁剪图`:'')}]}/>
        <Pagination current={itemPage} pageSize={20} total={detail.total} showSizeChanger={false} onChange={setItemPage}/></>}
    </>}]} />
  </Space>;
}
