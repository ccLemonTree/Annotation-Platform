import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Checkbox,Modal,Progress,Space,Spin,Typography} from 'antd';
import {isAxiosError} from 'axios';
import apiClient from '../api/client';
import type {ExportScope} from './ProjectExport';
import AssetImage from './AssetImage';

type Candidate={asset_id:string;name:string;reason:string};
type Recommendation={asset_id:string;name:string;text:string;candidates:Candidate[]};
type Job={id:string;state:'pending'|'running'|'ready'|'confirmed'|'failed';phase:'scan'|'confirm';error?:string;
  total:number|null;scanned:number;recommended:number;candidates:number;confirmed:number;skipped:number;reviewed:number;sample?:Recommendation[]};

export default function EmbeddingNegativeRecommendations({projectId,source,scope,approvedOnly,onIncludePending,onClose,onConfirmed,onGenerated}:{
  projectId:string;source:string;scope:ExportScope;approvedOnly:boolean;onIncludePending:()=>void;onClose:()=>void;onConfirmed:()=>void;
  onGenerated?:(id?:string)=>void;
}){
  const [job,setJob]=useState<Job|null>(null);
  const [excluded,setExcluded]=useState<Record<string,string[]>>({});
  const [reload,setReload]=useState(0),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const notified=useRef<string|undefined>(undefined);
  const automatic=!!onGenerated;
  const {work_job_id,image_kind}=scope;
  const url=`/api/export/projects/${projectId}/embedding-negative-recommendations/jobs`;
  useEffect(()=>{
    const controller=new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    setLoading(true);setError('');setExcluded({});
    const poll=async()=>{
      try{
        const {data}=await apiClient.post<Job|null>(url+'/status',{source,approved_only:approvedOnly,work_job_id,image_kind,automatic},
          {signal:controller.signal,skipErrorToast:true});
        if(controller.signal.aborted)return;
        setJob(data);
        if(data?.state==='pending'||data?.state==='running')timer=setTimeout(poll,1500);
      }catch(cause){if(!controller.signal.aborted)setError(errorText(cause));}
      finally{if(!controller.signal.aborted)setLoading(false);}
    };
    void poll();
    return()=>{controller.abort();clearTimeout(timer);};
  },[url,source,approvedOnly,work_job_id,image_kind,reload,automatic]);
  useEffect(()=>{
    if(automatic&&job?.state==='ready'&&job.candidates>0&&notified.current!==job.id){
      notified.current=job.id;onGenerated?.(job.id);
    }
    if(job?.state==='confirmed'&&job.confirmed>0&&notified.current!==job.id){
      notified.current=job.id;onConfirmed();
    }
  },[job,onConfirmed,onGenerated,automatic]);
  const running=job?.state==='pending'||job?.state==='running';
  const omissions=Object.entries(excluded).flatMap(([asset_id,ids])=>ids.map(negative_id=>({asset_id,negative_id})));
  const count=(job?.candidates||0)-omissions.length;
  const submit=async(confirm:boolean)=>{
    if(busy)return;
    setBusy(true);setError('');
    try{
      const {data}=await apiClient.post<Job>(confirm?`${url}/${job?.id}/confirm`:url,
        confirm?{excluded:omissions}:{source,approved_only:approvedOnly,work_job_id,image_kind,automatic},{skipErrorToast:true});
      if(automatic)onGenerated?.();
      setJob(data);setReload(value=>value+1);
    }catch(cause){setError(errorText(cause));}
    finally{setBusy(false);}
  };
  const reviewed=job?.phase==='confirm'?job.reviewed:job?.scanned||0;
  const total=job?.phase==='confirm'?job.recommended:job?.total;
  return <Modal title={automatic?'后台生成 InfoNCE 负样本':'后台推荐 InfoNCE 负样本'} open width={900} onCancel={()=>!busy&&onClose()}
    footer={<Space wrap>
      <Button disabled={busy} onClick={onClose}>关闭</Button>
      {!running&&<Button disabled={busy||loading} onClick={()=>void submit(false)}>{job?'重新生成':automatic?'开始后台生成':'开始后台推荐'}</Button>}
      {!automatic&&job?.state==='ready'&&<Button type="primary" loading={busy} disabled={loading||!count} onClick={()=>void submit(true)}>
        统一确认{count>0?` ${count} 张负图`:''}</Button>}
    </Space>}>
    <p>{automatic?'后台在导出范围内为每条文本生成最多 3 张明确不匹配的负图，生成后可直接导出或冻结。':'后台扫描整个导出范围，每条最多推荐 3 张。'}可关闭窗口，重新打开查看进度。</p>
    {automatic&&<p>{approvedOnly?'当前仅使用审核通过的标注。':'当前包含待审核标注。'}已驳回的图片会排除。</p>}
    {error&&<Alert type="error" title={error} action={<Button size="small" disabled={busy} onClick={()=>setReload(value=>value+1)}>刷新状态</Button>}/>}
    {loading&&!job&&<Spin/>}
    {job&&<Space orientation="vertical" style={{display:'flex'}}>
      <Typography.Text>已扫描 {job.scanned}{job.total!==null?` / ${job.total}`:''} 条 · 有推荐 {job.recommended} 条 · 候选负图 {job.candidates} 张次</Typography.Text>
      {running&&<Progress percent={total?Math.min(100,Math.floor(reviewed/total*100)):0}
        format={()=>job.phase==='confirm'?`正在确认 ${reviewed} / ${total||0}`:`正在扫描 ${reviewed}`}/>}
      {job.state==='failed'&&<Alert type="error" title={job.error||'后台任务失败，请重新生成'}/>}
      {job.state==='confirmed'&&<Alert type="success" title={`已确认 ${job.confirmed} 张次负图`}
        description={job.skipped?`有 ${job.skipped} 张次候选因标注或图片变化未确认。关闭窗口后可预览冻结范围。`:'关闭窗口后可预览冻结范围。'}/>}
      {job.state==='ready'&&!job.candidates&&<Alert type="info"
        title={job.scanned?'整个范围没有找到明确不匹配的候选':source!=='descriptions'?
          automatic&&!approvedOnly?'当前范围没有可用的目标属性':'当前范围没有审核通过的目标属性':'当前范围没有符合审核条件的图片描述'}
        action={!job.scanned&&source==='descriptions'&&approvedOnly&&<Button size="small" onClick={()=>{setJob(null);onIncludePending();}}>包含待审核描述</Button>}/>}
      {automatic&&job.state==='ready'&&job.candidates>0&&<Alert type="success"
        title={`已为 ${job.recommended} 条配对生成负样本`} description="关闭窗口后可下载 ZIP 或预览冻结范围。"/>}
      {!automatic&&job.state==='ready'&&!!job.sample?.length&&<>
        <Typography.Text>抽查前 {job.sample.length} 条推荐。取消勾选可排除对应候选，统一确认会处理其余全部候选。</Typography.Text>
        <div style={{maxHeight:'min(56vh, 540px)',overflowY:'auto',paddingRight:8}}>
          {job.sample.map(row=><section key={row.asset_id} style={{borderTop:'1px solid var(--ui-border)',padding:'12px 0'}}>
            <Space align="start" wrap>
              <AssetImage assetId={row.asset_id} alt={row.name} width={70} height={52} style={{objectFit:'contain'}}/>
              <Typography.Text strong>{row.text}</Typography.Text>
            </Space>
            <Space wrap size="middle" style={{display:'flex',marginTop:8}}>
              {row.candidates.map(candidate=><Checkbox key={candidate.asset_id}
                aria-label={`选择负样本 ${candidate.name}`} checked={!excluded[row.asset_id]?.includes(candidate.asset_id)} disabled={busy}
                onChange={event=>setExcluded(previous=>({...previous,[row.asset_id]:event.target.checked?
                  (previous[row.asset_id]||[]).filter(id=>id!==candidate.asset_id):[...(previous[row.asset_id]||[]),candidate.asset_id]}))}>
                <Space>
                  <AssetImage assetId={candidate.asset_id} alt={candidate.name} width={64} height={48} style={{objectFit:'contain'}}/>
                  <span>{candidate.reason}</span>
                </Space>
              </Checkbox>)}
            </Space>
          </section>)}
        </div>
      </>}
    </Space>}
  </Modal>;
}

function errorText(cause:unknown){
  const detail=isAxiosError(cause)?cause.response?.data?.detail:undefined;
  return typeof detail==='string'?detail:'后台推荐或确认失败，请重试';
}
