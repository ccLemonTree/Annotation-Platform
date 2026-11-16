import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Popconfirm, Progress, Select, Space, Table, Tag, Typography } from 'antd';
import { getCurrentUser } from '../api/auth';
import { getProjects } from '../api/projects';
import type { PaginatedResponse, Project } from '../api/projects';
import { getModelServices } from '../api/modelServices';
import type { ModelService } from '../api/modelServices';
import { cancelAutomationJob, getAutomationJob, getAutomationJobs, retryAutomationJob } from '../api/automation';
import type { AutomationJob } from '../api/automation';
import AutomationJobForm from '../components/automation/AutomationJobForm';
import AutomationJobDetails from '../components/automation/AutomationJobDetails';
import { automationStatusLabels } from '../components/automation/automationStatus';
import ProjectWorkflow from '../components/ProjectWorkflow';
import useAutomationFeed from '../components/automation/useAutomationFeed';
import AutomationReviewQueue from '../components/automation/AutomationReviewQueue';

const hasActiveJobs = (data: PaginatedResponse<AutomationJob>) => data.items.some(job => job.follow_uploads || job.status === 'pending' || job.status === 'running');

export default function AutomationPage({embeddedProject}:{embeddedProject?:Project}={}) {
  const [params,setParams]=useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]), [models, setModels] = useState<ModelService[]>([]);
  const [admin, setAdmin] = useState(false), [ready, setReady] = useState(false), [setupError, setSetupError] = useState('');
  const [setupRevision, setSetupRevision] = useState(0);
  const projectId=embeddedProject?.id||params.get('project_id')||undefined;
  const requestedJobId=embeddedProject?undefined:params.get('job_id')||undefined;
  const [page, setPage] = useState(1), [pageSize, setPageSize] = useState(20), [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false), [details, setDetails] = useState<AutomationJob>();
  const [busyId, setBusyId] = useState(''), [actionError, setActionError] = useState('');
  const writing = useRef(false), mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let active = true;
    (async () => {
      const user = await getCurrentUser();
      const allProjects: Project[] = embeddedProject?[embeddedProject]:[];
      let next = 1;
      while (!embeddedProject) {
        const result = await getProjects({ page: next, page_size: 100, task_type: 'detection' });
        if (!active) return;
        allProjects.push(...result.items);
        if (allProjects.length >= result.total) break;
        if (!result.items.length) throw Error('项目分页不完整');
        next++;
      }
      const services = user.role === 'admin' && !embeddedProject ? await getModelServices({ enabled: true }) : [];
      if (!active) return;
      setProjects(allProjects.filter(project => project.task_type === 'detection')); setModels(services);
      setAdmin(user.role === 'admin'); setReady(true);
    })().catch(() => { if (active) setSetupError('项目、权限或模型读取失败，请重试。'); });
    return () => { active = false; };
  }, [setupRevision,embeddedProject?.id]);
  const load = useCallback((signal: AbortSignal) => getAutomationJobs({ project_id: projectId, page, page_size: pageSize }, signal), [projectId, page, pageSize]);
  useEffect(()=>{
    if(!requestedJobId)return;
    setDetails(undefined);setActionError('');
    const controller=new AbortController();
    getAutomationJob(requestedJobId,controller.signal).then(job=>{if(!controller.signal.aborted){if(projectId&&job.project_id!==projectId){setActionError('来源任务不属于当前项目');return;}setDetails(job);}}).catch(()=>{if(!controller.signal.aborted)setActionError('来源任务读取失败，请重试');});
    return()=>controller.abort();
  },[requestedJobId,projectId]);
  const { data, loading, error } = useAutomationFeed(`${projectId || ''}:${page}:${pageSize}`, load, hasActiveJobs, revision);
  const refresh = () => setRevision(n => n + 1);
  const changeJob = async (job: AutomationJob, action: 'cancel' | 'retry') => {
    if (!admin || writing.current) return;
    writing.current = true; setBusyId(job.id); setActionError('');
    try {
      if (action === 'cancel') await cancelAutomationJob(job.id); else await retryAutomationJob(job.id);
      if (mounted.current) refresh();
    } catch { if (mounted.current) setActionError('任务操作失败，请刷新状态后重试。'); }
    finally { writing.current = false; if (mounted.current) setBusyId(''); }
  };
  const currentDetails = data?.items.find(job => job.id === details?.id) || details;
  const currentProject=projects.find(project=>project.id===projectId);
  const queueRevision=`${revision}:${data?.items.map(job=>`${job.id}:${job.needs_review_count}:${job.status}`).join(',')}`;
  const openQueueJob=async(id:string)=>{
    try{const job=await getAutomationJob(id);if(job.project_id!==projectId){setActionError('来源任务不属于当前项目');return;}setDetails(job);}
    catch{setActionError('来源任务读取失败，请重试');}
  };
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {!embeddedProject&&currentProject&&<ProjectWorkflow project={currentProject} active="prepare"/>}
    <Alert type="info" showIcon title="预标注只处理未标注图片，已有标注不覆盖；AI审核只处理已有标注，不改人工框。"
      description="所有框置信度均高于阈值，且整图完整性与外扩框检查均通过，才可自动通过。低分、不确定及全部负样本交管理员最终审核，零框不能自动判负。" />
    {ready && !admin && <Alert type="info" title="当前为只读模式，任务创建、取消、重试及最终审核仅管理员可操作。" />}
    {setupError && <Alert type="error" title={setupError} action={<Button onClick={() => { setSetupError(''); setSetupRevision(n => n + 1); }}>重试初始化</Button>} />}
    <Space wrap>
      {!embeddedProject&&<><label htmlFor="automation-project">项目</label>
      <Select id="automation-project" aria-label="筛选项目" placeholder="全部项目" allowClear showSearch optionFilterProp="label"
        loading={!ready && !setupError} value={projectId} style={{ width: 260, maxWidth: '100%' }}
        options={projects.map(project => ({ value: project.id, label: project.project_name }))}
        onChange={id => { const next=new URLSearchParams(params);if(id)next.set('project_id',id);else next.delete('project_id');next.delete('job_id');setParams(next);setPage(1); setDetails(undefined); setActionError(''); }} /></>}
      <Button loading={loading} onClick={refresh}>刷新任务</Button>
      {admin && ready && (embeddedProject?<Link to={`/projects/${embeddedProject.id}/prepare`}>在工作流中创建预标注或 AI 审核</Link>:<Button type="primary" onClick={() => setCreating(true)} disabled={!projects.length}>创建任务</Button>)}
      {!embeddedProject&&projectId&&<Link to={`/annotations?project_id=${projectId}`}>查看项目审核 →</Link>}
      {!embeddedProject&&admin&&<Link to={`/review-queue${projectId?`?project_id=${projectId}`:''}`}>管理员待审队列 →</Link>}
    </Space>
    {error && <Alert type="error" showIcon title="任务读取失败，请重试；当前显示可能已过期。" />}
    {actionError && <Alert type="error" title={actionError} />}
    <Card title="批量任务" styles={{ body: { minWidth: 0 } }}>
      <Typography.Paragraph type="secondary">等待中 / 进行中任务每 3 秒刷新，请求结束后计时；失败时可手动重试读取。</Typography.Paragraph>
      <Table rowKey="id" loading={loading} dataSource={data?.items || []} scroll={{ x: 1150 }} columns={[
        { title: '任务 ID', dataIndex: 'id', width: 240 },
        { title: '项目', dataIndex: 'project_id', width: 180, render: id => projects.find(project => project.id === id)?.project_name || id },
        { title: '类型', dataIndex: 'kind', width: 120, render: kind => kind === 'preannotate' ? '批量预标注' : 'AI审核' },
        { title: '状态', dataIndex: 'status', width: 145, render: (status,job) => <Space wrap><Tag color={status === 'failed' ? 'error' : status === 'completed' ? 'success' : 'processing'}>{automationStatusLabels[status] || status}</Tag>{job.follow_uploads&&<Tag color="processing">接收新上传</Tag>}</Space> },
        { title: '进度', width: 180, render: (_, job) => <><span>{job.processed} / {job.total}</span><Progress percent={job.total ? Math.min(100, Math.round(job.processed / job.total * 100)) : 0} size="small" /></> },
        { title: '通过 / 待审 / 失败', width: 150, render: (_, job) => `${job.approved_count} / ${job.needs_review_count} / ${job.failed_count}` },
        { title: '创建时间', dataIndex: 'created_at', width: 180, render: value => new Date(value).toLocaleString('zh-CN') },
        { title: '错误', dataIndex: 'error', width: 200, render: value => value || '—' },
        { title: '操作', width: 230, render: (_, job) => <Space wrap>
          <Button onClick={() => setDetails(job)}>查看明细</Button>
          {admin && (job.status === 'pending' || job.status === 'running') && <Popconfirm title="确认取消此任务？已处理结果会保留。" okText="确认取消" cancelText="继续任务" onConfirm={() => changeJob(job, 'cancel')}>
            <Button loading={busyId === job.id} disabled={!!busyId}>取消任务</Button>
          </Popconfirm>}
          {admin && (job.status === 'failed' || job.status === 'canceled') && <Button loading={busyId === job.id} disabled={!!busyId} onClick={() => { void changeJob(job, 'retry'); }}>重试任务</Button>}
        </Space> },
      ]} pagination={{ current: page, pageSize, total: data?.total || 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100],
        onChange: (next, size) => { setPage(size === pageSize ? next : 1); setPageSize(size); setDetails(undefined); } }} />
    </Card>
    {embeddedProject&&admin&&<AutomationReviewQueue projectId={embeddedProject.id} revision={queueRevision} onOpenJob={openQueueJob} onResolved={refresh}/>}
    {creating && admin && <AutomationJobForm projects={projects} models={models} projectId={projectId} fixedProject={!!embeddedProject} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); setPage(1); refresh(); }} />}
    {currentDetails && <AutomationJobDetails key={currentDetails.id} job={currentDetails} onFollowSaved={admin?enabled=>{
      setDetails({...currentDetails,follow_uploads:enabled});refresh();
    }:undefined} onClose={() => {setDetails(undefined);if(!embeddedProject){const next=new URLSearchParams(params);next.delete('job_id');setParams(next,{replace:true});}}} />}
  </Space>;
}
