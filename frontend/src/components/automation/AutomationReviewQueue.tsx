import {useCallback,useState} from 'react';
import {Link} from 'react-router-dom';
import {Alert,Button,Card,Space,Table,Tag} from 'antd';
import {getAutomationReviewQueue,type AutomationItem} from '../../api/automation';
import AutomationReviewPreview from './AutomationReviewPreview';
import useAutomationFeed from './useAutomationFeed';

const noPolling=()=>false;
export default function AutomationReviewQueue({projectId,revision=0,onOpenJob,onResolved}:{projectId?:string;revision?:string|number;onOpenJob?:(id:string)=>void;onResolved?:()=>void}){
  const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(20),[reload,setReload]=useState(0);
  const [preview,setPreview]=useState<AutomationItem>();
  const load=useCallback((signal:AbortSignal)=>getAutomationReviewQueue({project_id:projectId,page,page_size:pageSize},signal),[projectId,page,pageSize]);
  const {data,loading,error}=useAutomationFeed(`${projectId||''}:${page}:${pageSize}`,load,noPolling,`${revision}:${reload}`);
  return <Card title="AI转人工待审" extra={<Button loading={loading} onClick={()=>setReload(value=>value+1)}>刷新待审</Button>}>
    {error&&<Alert type="error" title="待审队列读取失败，请重试。"/>}
    <Table rowKey="id" loading={loading} dataSource={data?.items||[]} locale={{emptyText:error?'队列暂不可用':'暂无 AI 转交的待审图片'}} scroll={{x:1100}} columns={[
      {title:'项目',width:180,render:(_,item)=>item.project_id&&!onOpenJob?<Link to={`/projects/${item.project_id}/images`}>{item.project_name||item.project_id}</Link>:item.project_name||'归属读取失败'},
      {title:'图片名称',width:240,render:(_,item)=><Space orientation="vertical" size={0}><span>{item.asset_name||'原图已移除'}</span><small style={{color:'var(--ui-muted)'}}>资产 {item.asset_id}</small></Space>},
      {title:'来源任务',width:190,render:(_,item)=><Space orientation="vertical" size={0}><Tag>{item.job_kind==='preannotate'?'批量预标注':item.job_kind==='review'?'AI审核':'历史任务'}</Tag>{onOpenJob?<Button type="link" onClick={()=>onOpenJob(item.job_id)}>查看来源任务</Button>:<Link to={`/automation?${new URLSearchParams({...(item.project_id?{project_id:item.project_id}:{}),job_id:item.job_id})}`}>查看来源任务</Link>}<small style={{color:'var(--ui-muted)'}}>{item.job_id}</small></Space>},
      {title:'原因',dataIndex:'reason'}, {title:'版本',dataIndex:'annotation_version',render:value=>value??'—'},
      {title:'操作',render:(_,item)=><Button onClick={()=>setPreview(item)}>预览审核</Button>},
    ]} pagination={{current:page,pageSize,total:data?.total||0,showSizeChanger:true,pageSizeOptions:[20,50,100],onChange:(next,size)=>{setPage(size===pageSize?next:1);setPageSize(size);}}}/>
    {preview&&<AutomationReviewPreview key={preview.id} item={preview} projectId={projectId} from="automation-review" onClose={()=>setPreview(undefined)} onResolved={()=>{setPreview(undefined);setPage(1);setReload(value=>value+1);onResolved?.();}}/>}
  </Card>;
}
