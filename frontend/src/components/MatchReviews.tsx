import { useEffect,useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert,Button,Card,Input,Select,Space,Table,Tag,Typography } from 'antd';
import { createMatchReviewBatch,getMatchQueries,getWorkJobs,reviewMatch,type MatchQuery,type MatchHit,type WorkJob } from '../api/projectWork';
import { getModelServices,type ModelService } from '../api/modelServices';
import AssetImage from './AssetImage';
import JsonView from './JsonView';

const statuses={pending:'待审核',approved:'已通过',rejected:'已驳回'};
export default function MatchReviews({projectId}:{projectId:string}){
  const [queries,setQueries]=useState<MatchQuery[]>([]),[page,setPage]=useState(1),[reload,setReload]=useState(0),[loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[error,setError]=useState(''),[status,setStatus]=useState<string>();
  const [models,setModels]=useState<ModelService[]>([]),[model,setModel]=useState<string>(),[prompt,setPrompt]=useState(''),[scope,setScope]=useState('all'),[info,setInfo]=useState(''),[jobs,setJobs]=useState<WorkJob[]>([]);
  const [admin]=useState(()=>{try{return JSON.parse(localStorage.getItem('user')||'null')?.role==='admin';}catch{return false;}});
  const fail=(cause:unknown)=>{const error=cause as {response?:{data?:{detail?:unknown}}};setError(typeof error.response?.data?.detail==='string'?error.response.data.detail:'读取或审核失败，请刷新重试');};
  useEffect(()=>{let active=true;getModelServices({enabled:true}).then(rows=>{if(active)setModels(rows.filter(m=>m.kind==='caption'&&m.protocol==='openai_chat'));}).catch(cause=>{if(active)fail(cause);});return()=>{active=false;};},[]);
  useEffect(()=>{let active=true,pending=false;
    const load=async()=>{if(pending)return;pending=true;try{
      const [rows,jobs]=await Promise.all([getMatchQueries(projectId,page),getWorkJobs(projectId)]);
      if(active){setQueries(current=>rows.map(row=>{const newer=current.find(q=>q.id===row.id&&q.version>row.version);return newer||row;}));setJobs(jobs.filter(job=>job.operation==='match_review'));}
    }catch(cause){if(active)fail(cause);}finally{pending=false;if(active)setLoading(false);}};
    void load();const timer=setInterval(load,3000);return()=>{active=false;clearInterval(timer);};
  },[projectId,page,reload]);
  const rows=queries.flatMap(query=>query.hits.filter(hit=>hit.judgment!=='unknown').map(hit=>({key:query.id+hit.asset_id,query,hit})));
  const needsManual=(hit:MatchHit)=>(hit.review_status||'pending')==='pending'&&hit.ai_review?.status==='needs_review';
  const audit=async()=>{if(!model)return;setBusy('ai');setError('');setInfo('');try{
    const batches=await createMatchReviewBatch(projectId,{model_id:model,prompt,...(scope==='page'?{query_ids:queries.map(q=>q.id)}:{})});
    setInfo(`已排队 ${batches.reduce((n,job)=>n+job.total,0)} 条匹配关系，完成后自动更新；可在后台处理队列取消或重试。`);setReload(v=>v+1);
  }catch(cause){fail(cause);}finally{setBusy('');}};
  const review=async(query:MatchQuery,hit:MatchHit,approved:boolean)=>{setBusy(query.id+hit.asset_id);setError('');
    try{const updated=await reviewMatch(projectId,query,hit.asset_id,approved);setQueries(rows=>rows.map(row=>row.id===updated.id?updated:row));}catch(cause){fail(cause);}finally{setBusy('');}};
  const changePage=(next:number)=>{setLoading(true);setError('');setQueries([]);setPage(next);};
  return <Card title="检索匹配标注审核" style={{marginTop:20,marginBottom:20}} extra={<Button disabled={loading||!!busy} onClick={()=>{setLoading(true);setError('');setReload(v=>v+1);}}>刷新匹配判定</Button>}>
    <Typography.Paragraph type="secondary">正确 / 错误针对每次查询单独保存，不计入图片描述或分类数量。历史匹配判定也可在此审核；修改判定后重新待审，已驳回关系不参与新导出的训练数据。</Typography.Paragraph>
    {admin&&<Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
      <Typography.Text>大模型独立判断：与人工一致自动通过（包括共同判错），分歧或无法判断转人工复核。</Typography.Text>
      <Space wrap><Select aria-label="匹配大模型审核模型" placeholder="选择视觉对话审核模型" value={model} disabled={!!busy} onChange={setModel} style={{width:280,maxWidth:'100%'}} options={models.map(m=>({value:m.id,label:`${m.name} · r${m.revision}`}))}/>
        <Select aria-label="匹配大模型审核范围" value={scope} disabled={!!busy} onChange={setScope} style={{width:240,maxWidth:'100%'}} options={[{value:'all',label:'当前项目全部待审匹配'},{value:'page',label:'本页查询中的待审匹配'}]}/>
        <Button type="primary" aria-label="大模型审核待审匹配" loading={busy==='ai'} disabled={!model||loading||!!busy||(scope==='page'&&!queries.length)} onClick={audit}>大模型审核待审匹配</Button><Link to="/models">编辑模型核心提示词</Link></Space>
      <Input.TextArea aria-label="匹配大模型审核补充要求" value={prompt} disabled={!!busy} onChange={e=>setPrompt(e.target.value)} maxLength={2000} autoSize={{minRows:2,maxRows:5}} placeholder="本批补充审核要求（可选），例如只核对上衣颜色，不根据阴影猜测"/>
      {!!jobs.length&&<Typography.Text type="secondary">最近匹配审核批次：排队 {jobs.reduce((n,j)=>n+(j.counts.pending||0),0)} · 处理中 {jobs.reduce((n,j)=>n+(j.counts.running||0),0)} · 完成 {jobs.reduce((n,j)=>n+(j.counts.completed||0),0)} · 失败 {jobs.reduce((n,j)=>n+(j.counts.failed||0),0)}</Typography.Text>}
    </Space>}
    {info&&<Alert type="success" title={info} style={{marginBottom:12}}/>}
    {error&&<Alert type="error" title={error} style={{marginBottom:12}}/>}
    <Space wrap style={{marginBottom:16}}><Typography.Text>本页查询 {queries.length} 条 · 已判定关系 {rows.length} 条</Typography.Text>
      {Object.entries(statuses).map(([key,label])=><Tag key={key}>{label} {rows.filter(row=>(row.hit.review_status||'pending')===key).length}</Tag>)}
      <Tag color="warning">待人工复核 {rows.filter(row=>needsManual(row.hit)).length}</Tag>
      <Select aria-label="匹配审核状态" placeholder="全部审核状态" allowClear value={status} onChange={setStatus} style={{width:160}} options={[...Object.entries(statuses).map(([value,label])=>({value,label})),{value:'needs_review',label:'待人工复核'}]}/>
      <Link to={`/annotate/project/${projectId}`}>前往匹配工作台修改判定</Link>
    </Space>
    <Table rowKey="key" loading={loading} dataSource={rows.filter(row=>!status||(status==='needs_review'?needsManual(row.hit):(row.hit.review_status||'pending')===status))} pagination={false} scroll={{x:1220}}
      locale={{emptyText:error?'读取失败，请刷新重试':status?'本页查询暂无符合审核状态的匹配判定':'本页查询暂无正确 / 错误判定，请前往匹配工作台标注'}} columns={[
        {title:'预览',width:100,render:(_,row)=><AssetImage assetId={row.hit.asset_id} alt={row.hit.original_name} width={64} height={72} style={{objectFit:'contain'}}/>},
        {title:'查询',width:260,render:(_,row)=><><div>{row.query.query.text||'以图搜图'}</div><Typography.Text type="secondary">{row.query.id.slice(0,8)} · v{row.query.version}</Typography.Text></>},
        {title:'图片',dataIndex:['hit','original_name'],width:220,ellipsis:true},
        {title:'匹配判定',width:110,render:(_,row)=><Tag color={row.hit.judgment==='correct'?'success':'error'}>{row.hit.judgment==='correct'?'正确':'错误'}</Tag>},
        {title:'大模型结论 / 理由',width:300,render:(_,row)=>row.hit.ai_review?<><Tag color={row.hit.ai_review.status==='approved'?'success':row.hit.ai_review.status==='needs_review'?'warning':'default'}>{({correct:'匹配',incorrect:'不匹配',uncertain:'无法判断'})[row.hit.ai_review.judgment]}</Tag><Typography.Paragraph style={{marginBottom:4}} ellipsis={{rows:2,expandable:true}}>{row.hit.ai_review.reason}</Typography.Paragraph><Typography.Text type="secondary">{row.hit.ai_review.model_name} · r{row.hit.ai_review.revision}</Typography.Text><details><summary>完整AI审核记录</summary><JsonView label="匹配AI审核记录" value={row.hit.ai_review}/></details></>:'尚未AI审核'},
        {title:'审核状态',width:110,render:(_,row)=><Tag color={needsManual(row.hit)?'warning':row.hit.review_status==='approved'?'success':'default'}>{needsManual(row.hit)?'待人工复核':statuses[row.hit.review_status||'pending']}</Tag>},
        {title:'审核操作',width:180,render:(_,row)=><Space><Button aria-label="通过匹配审核" disabled={!!busy||row.hit.review_status==='approved'} loading={busy===row.key} onClick={()=>review(row.query,row.hit,true)}>通过</Button><Button aria-label="驳回匹配审核" danger disabled={!!busy||row.hit.review_status==='rejected'} onClick={()=>review(row.query,row.hit,false)}>驳回</Button></Space>},
      ]}/>
    <Space wrap style={{marginTop:16}}><Button disabled={loading||!!busy||page===1} onClick={()=>changePage(page-1)}>上一页匹配查询</Button><span>第 {page} 页查询 · 每页最多 20 条查询</span><Button disabled={loading||!!busy||queries.length<20} onClick={()=>changePage(page+1)}>下一页匹配查询</Button></Space>
  </Card>;
}
