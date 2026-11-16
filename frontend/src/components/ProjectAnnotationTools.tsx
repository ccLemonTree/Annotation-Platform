import { useEffect,useRef,useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert,Button,Card,Checkbox,Drawer,Pagination,Popconfirm,Progress,Space,Table,Tag,Typography } from 'antd';
import { isAxiosError } from 'axios';
import type { Project } from '../api/projects';
import { deleteWorkJob,followWorkUploads,getWork,getWorkJobs,workAction,type WorkJob,type WorkDetail } from '../api/projectWork';
import AssetImage from './AssetImage';

const operations={crop:'检测切图',caption:'生成描述',index:'建立向量索引',detect_attributes:'检测切图并预标注属性',attributes:'大模型预标注属性',quality:'图片质量检查'};
const workLabels={...operations,match_review:'匹配大模型审核',detect:'目标检测筛选',preannotate:'目标检测预标注',ai_review:'AI 审核',filter:'大模型筛选'};
const canCancel=(job:WorkJob)=>!!(job.counts.pending||job.counts.running||job.pipeline?.attributes.counts.pending||job.pipeline?.attributes.counts.running);
const statuses:Record<string,string>={pending:'等待',running:'处理中',completed:'完成',failed:'失败',cancelled:'已取消'};
function WorkProgress({title,total,counts,unit,detailed}:{title?:string;total:number;counts:Record<string,number>;unit:string;detailed:boolean}){
  const completed=counts.completed||0,failed=counts.failed||0,cancelled=counts.cancelled||0;
  const handled=completed+failed+cancelled;
  return <Space orientation="vertical" size={0} style={{width:'100%'}}>
    <Space wrap size={[8,0]}>{title&&<Typography.Text strong>{title}</Typography.Text>}
      <span>已处理 {handled} / {total} {unit}</span>
      {!detailed&&<>{!!counts.running&&<Typography.Text type="secondary">处理中 {counts.running}</Typography.Text>}
        {!!counts.pending&&<Typography.Text type="secondary">等待 {counts.pending}</Typography.Text>}
        {!!failed&&<Typography.Text type="danger">失败 {failed}</Typography.Text>}
        {!!cancelled&&<Typography.Text type="secondary">已取消 {cancelled}</Typography.Text>}</>}
    </Space>
    <Progress percent={total?Math.floor(handled*100/total):0} showInfo={false} size="small" status={failed?'exception':counts.running?'active':'normal'}/>
    {detailed&&<Typography.Text type="secondary">成功 {completed} · 失败 {failed} · 已取消 {cancelled} · 处理中 {counts.running||0} · 等待 {counts.pending||0}</Typography.Text>}
  </Space>;
}
function JobProgress({job,detailed=false}:{job:WorkJob;detailed?:boolean}){
  return <Space orientation="vertical" size={detailed?12:0} style={{width:'100%'}}>
    {job.pipeline?<>
      <WorkProgress title="检测" {...job.pipeline.detection} unit="张原图" detailed={detailed}/>
      <WorkProgress title="属性" {...job.pipeline.attributes} unit="个目标" detailed={detailed}/>
    </>:<WorkProgress total={job.total} counts={job.counts} unit={job.operation==='attributes'?'个目标':'张图片'} detailed={detailed}/>}
    {detailed&&job.waiting_reason&&<Typography.Text type="warning">{job.waiting_reason}</Typography.Text>}
    {detailed&&job.failure_reasons?.map(failure=><Typography.Text type="danger" key={failure.operation+failure.reason}>{workLabels[failure.operation]}：{failure.reason}（{failure.count} 项）</Typography.Text>)}
  </Space>;
}
export default function ProjectAnnotationTools({project,onRefresh}:{project:Project;onRefresh:()=>void}){
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const [jobs,setJobs]=useState<WorkJob[]>([]),[jobPage,setJobPage]=useState(1),[jobId,setJobId]=useState<string>(),[job,setJob]=useState<WorkDetail>(),[itemPage,setItemPage]=useState(1);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const qualityActive=useRef(new Map<string,boolean>());
  const refreshCompletedQuality=(rows:WorkJob[])=>{
    let removed=false;
    for(const row of rows){
      if(row.operation!=='quality')continue;
      const active=!!(row.counts.pending||row.counts.running);
      if(qualityActive.current.get(row.id)&&!active&&row.quality_counts?.deleted)removed=true;
      qualityActive.current.set(row.id,active);
    }
    if(removed)onRefresh();
  };
  const fail=(error:unknown)=>{if(mounted.current)setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'操作失败，请重试');};
  useEffect(()=>{let active=true,inFlight=false;
    const load=async()=>{if(inFlight)return;inFlight=true;try{const rows=await getWorkJobs(project.id,jobPage);if(active){setJobs(rows);refreshCompletedQuality(rows);}}catch(error){if(active)fail(error);}finally{inFlight=false;}};
    void load();const timer=setInterval(load,3000);return()=>{active=false;clearInterval(timer);};
  },[project.id,jobPage,reload]);
  useEffect(()=>{if(!jobId)return;let active=true,inFlight=false;
    const load=async()=>{if(inFlight)return;inFlight=true;try{const detail=await getWork(project.id,jobId,itemPage);if(active){setJob(detail);refreshCompletedQuality([detail]);}}catch(error){if(active)fail(error);}finally{inFlight=false;}};
    void load();const timer=setInterval(load,3000);return()=>{active=false;clearInterval(timer);};
  },[project.id,jobId,itemPage,reload]);
  const action=async(action:'cancel'|'retry',target:WorkJob)=>{if(busy)return;setBusy(true);setError('');try{await workAction(project.id,target.id,action);if(mounted.current){if(action==='retry'&&target.operation==='quality')qualityActive.current.set(target.id,true);setReload(v=>v+1);}}catch(error){fail(error);}finally{if(mounted.current)setBusy(false);}};
  const removeJob=async(target:WorkJob)=>{
    setBusy(true);setError('');
    try{
      await deleteWorkJob(project.id,target.id);
      if(!mounted.current)return;
      setJobId(undefined);
      setJobs(rows=>rows.filter(row=>row.id!==target.id));
      if(jobPage>1&&jobs.length===1&&jobs[0].id===target.id)setJobPage(jobPage-1);
      setReload(v=>v+1);
    }catch(error){fail(error);}finally{if(mounted.current)setBusy(false);}
  };
  const setFollow=async(enabled:boolean)=>{if(!jobId)return;setBusy(true);setError('');
    try{await followWorkUploads(project.id,jobId,enabled);if(mounted.current)setReload(v=>v+1);}
    catch(error){fail(error);}finally{if(mounted.current)setBusy(false);}
  };
  const detail=job?.id===jobId&&job?.page===itemPage?job:undefined;
  const detailsOpen=!!jobId;
  const showJob=(id:string)=>{setJobId(id);setItemPage(1);setError('');};
  return <Card size="small"><Space className="project-annotation-tools" orientation="vertical" size="middle" style={{width:'100%',minWidth:0}}>
      {error&&!detailsOpen&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>{setError('');setReload(v=>v+1);}}>刷新重试</Button>}/>}
      <Space wrap style={{display:'flex',justifyContent:'space-between',marginBottom:12}}>
        <Typography.Text type="secondary">每行是一次历史任务；已归并的内部批次统一显示进度。</Typography.Text>
        <Space><Button disabled={jobPage===1} onClick={()=>setJobPage(v=>v-1)}>上一页</Button><span>第 {jobPage} 页</span><Button disabled={jobs.length<20} onClick={()=>setJobPage(v=>v+1)}>下一页</Button></Space>
      </Space>
      <Table<WorkJob> size="small" rowKey="id" dataSource={jobs} pagination={false} scroll={{x:960,y:'min(48vh, 420px)'}} locale={{emptyText:'此页暂无批次'}} columns={[
        {title:'处理流程',width:230,render:(_,row)=><Space orientation="vertical" size={0} style={{maxWidth:'100%'}}>
          <Typography.Text strong>{workLabels[row.operation]}</Typography.Text>
          {!!row.target_labels?.length&&<Typography.Text type="secondary" ellipsis={{tooltip:row.target_labels.join('、')}} style={{maxWidth:195}}>目标：{row.target_labels.join('、')}</Typography.Text>}
          {row.batch_count&&<Tag>合并 {row.batch_count} 批</Tag>}
          {row.follow_uploads&&<Tag color="processing">接收新上传</Tag>}
        </Space>},
        {title:'阶段进度',render:(_,row)=><JobProgress job={row}/>},
        {title:'模型 / 创建时间',width:200,render:(_,row)=><Space orientation="vertical" size={0}><div><span>{row.model_name||'模型'}</span> <Tag>r{row.revision}</Tag></div>
          <Typography.Text type="secondary">{row.created_at?new Date(row.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—'}</Typography.Text></Space>},
        {title:'操作',width:244,render:(_,row)=><Space size={0} wrap><Button type="link" size="small" onClick={()=>showJob(row.id)}>查看明细</Button>
          <Popconfirm title="确认取消此批次？" description={row.follow_uploads?'取消未完成项并停止接收新上传图片，已完成的结果保留。':'仅取消等待中和处理中的项，已完成的结果保留。'} okText="确认取消" cancelText="返回" okButtonProps={{danger:true}} onConfirm={()=>action('cancel',row)}>
            <Button type="link" size="small" danger disabled={busy||!canCancel(row)}>取消任务</Button>
          </Popconfirm>
          {(row.operation==='crop'||row.operation==='detect_attributes')&&!canCancel(row)&&!!row.counts.completed&&<Space size={6}>
            <Link to={`/projects/${project.id}/images?work_job_id=${row.id}`}>查看裁剪结果</Link>
            <Link to={`/projects/${project.id}/prepare?source_job_id=${row.id}`}>用于新工作流</Link>
          </Space>}</Space>}]}/>
      <Drawer open={detailsOpen} size={780} styles={{wrapper:{maxWidth:'100vw'}}} destroyOnHidden
        title={detail?`${workLabels[detail.operation]} · 批次明细`:'批次明细'} loading={!detail&&!error}
        onClose={()=>{setJobId(undefined);setError('');}}>
      {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>{setError('');setReload(v=>v+1);}}>刷新重试</Button>} style={{marginBottom:12}}/>}
      {detail&&<><Space orientation="vertical" size="small" style={{display:'flex',marginBottom:16}}>
        <Typography.Text type="secondary">批次 {detail.id.slice(0,8)} · {detail.model_name||'模型'} r{detail.revision}{detail.attribute_model_name&&` · ${detail.attribute_model_name}`}</Typography.Text>
        {!!detail.target_labels?.length&&<Typography.Text>目标：{detail.target_labels.join('、')}</Typography.Text>}
        {!!detail.attribute_fields?.length&&<Typography.Text>属性：{detail.attribute_fields.join('、')}</Typography.Text>}
        {detail.pipeline&&<Typography.Text>已产生 {detail.pipeline.generated_targets} 个目标 · 保留 {detail.pipeline.preserved_annotations} 个已有标注</Typography.Text>}
        {detail.quality_counts&&<Typography.Text>合格 {detail.quality_counts.passed} 张 · 已删除 {detail.quality_counts.deleted} 张</Typography.Text>}
        {!!(detail.min_crop_long_edge||detail.filtered_crop_count)&&<Typography.Text>最小长边 {detail.min_crop_long_edge||0} px · 尺寸过滤 {detail.filtered_crop_count||0} 张</Typography.Text>}
        <JobProgress job={detail} detailed/>
      </Space><Space wrap>
        <Button danger disabled={busy||!canCancel(detail)} onClick={()=>action('cancel',detail)}>取消未完成项</Button>
        <Button disabled={busy||!(detail.counts.failed||detail.counts.cancelled)} onClick={()=>action('retry',detail)}>{detail.pipeline?'重试失败与取消原图':'重试失败与取消项'}</Button>
        {!detail.parent&&<Popconfirm disabled={busy||canCancel(detail)||!!detail.follow_uploads}
          title="删除此任务？" description="任务将从队列移除。已生成的图片、标注和索引会保留。" okText="确认删除" cancelText="返回"
          okButtonProps={{danger:true}} onConfirm={()=>removeJob(detail)}>
          <Button danger disabled={busy||canCancel(detail)||!!detail.follow_uploads}>删除任务</Button>
        </Popconfirm>}
      </Space>
        {!detail.parent&&!detail.batch_count&&detail.operation!=='match_review'&&(detail.operation!=='crop'||detail.crop_from_originals||detail.follow_uploads)&&<div style={{marginBlock:12}}>
          <Checkbox checked={!!detail.follow_uploads} disabled={busy} onChange={event=>void setFollow(event.target.checked)}>自动加入新上传图片</Checkbox>
          <p className="workspace-help">{detail.operation==='quality'?'新图入队后自动质检，不合格的项目图片及标注会删除。':'新图沿用本批次的模型和处理规则。'}关闭后，已入队的图片继续处理。</p>
        </div>}
        {detail.operation==='quality'&&<Alert type="info" title="本批质检要求" description={detail.quality_requirement} style={{margin:'12px 0'}}/>}
        {detail.parent&&<Typography.Paragraph>来源原图：{detail.parent.original_name||'已移除'} <Button type="link" onClick={()=>showJob(detail.parent!.job_id)}>返回所属分析流程</Button></Typography.Paragraph>}
        {detail.example&&<Typography.Paragraph>本批正确范例：<Link to={`/annotate/${detail.example.asset_id}?from=attributes`} target="_blank" rel="noopener noreferrer">{detail.example.original_name}</Link> · 人工标注 v{detail.example.annotation_version}</Typography.Paragraph>}
        <Table size="small" rowKey="id" dataSource={detail.items} pagination={false} scroll={{x:600}} style={{marginBlock:12}} columns={[
          {title:'图片',render:(_,item)=>item.asset_id?<Space><AssetImage assetId={item.asset_id} width={64} height={48} style={{objectFit:'contain'}}/><Link to={`/annotate/${item.asset_id}`}>{item.original_name||item.asset_id.slice(0,8)}</Link></Space>:item.original_name||item.result?.original_name||'已移除'},
          {title:'状态',render:(_,item)=>detail.operation==='quality'&&item.status==='failed'?<Tag color="error">{item.asset_id?'失败 · 图片保留':'失败 · 图片已移除'}</Tag>:item.result?.deleted?<Tag color="error">不合格 · 已删除</Tag>:detail.operation==='quality'&&item.result?.passed===true?<Tag color="success">{item.asset_id?'合格 · 保留':'合格 · 图片已移除'}</Tag>:statuses[item.status]||item.status},{title:'结果',render:(_,item)=><Space wrap>{item.error||item.result?.reason||item.result?.warning||item.result?.description||(item.result?.attributes?`已分析 ${Object.keys(item.result.attributes).length} 项属性`:item.result?.detected_count!==undefined?`生成 ${item.result.detected_count} 张裁剪图`:'')}{item.result?.attribute_job_id&&<Button type="link" onClick={()=>{setJobId(item.result!.attribute_job_id);setItemPage(1);}}>查看逐目标属性批次</Button>}{!!item.result?.filtered_count&&<span>尺寸过滤 {item.result.filtered_count} 张</span>}{!!item.result?.skipped_annotated_count&&<span>保留 {item.result.skipped_annotated_count} 张已有标注</span>}</Space>}]}/>
        <Pagination current={itemPage} pageSize={20} total={detail.total} showSizeChanger={false} onChange={setItemPage}/></>}
      </Drawer>
  </Space></Card>;
}
