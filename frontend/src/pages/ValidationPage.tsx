import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Modal, Space, Table, Tag, Typography, Tabs } from 'antd';
import { getCurrentUser } from '../api/auth';
import { getProjects } from '../api/projects';
import type { PaginatedResponse, Project } from '../api/projects';
import { activeValidationRun, changeValidationRun, deleteValidationRun, deleteValidationSet, refreshValidationSet, getValidationRuns, getValidationSets, validationError, validationStatusLabels } from '../api/validation';
import type { ValidationRun, ValidationSet } from '../api/validation';
import useAutomationFeed from '../components/automation/useAutomationFeed';
import { CreateValidationRun, CreateValidationSet } from '../components/validation/ValidationForms';
import ValidationResults from '../components/validation/ValidationResults';
import MultimodalWorkspace from '../components/validation/MultimodalWorkspace';

export default function ValidationPage(){
  const [params,setParams]=useSearchParams();
  const kind=params.get('task_type')||'detection';
  return <Tabs activeKey={kind} onChange={value=>setParams(previous=>{const next=new URLSearchParams(previous);next.set('task_type',value);next.delete('semantic_run_id');return next;},{preventScrollReset:true})} items={[
    {key:'detection',label:'目标检测',children:<DetectionValidationPage/>},
    {key:'caption',label:'大模型图片描述',children:<MultimodalWorkspace key="caption" kind="caption"/>},
    {key:'embedding',label:'向量检索',children:<MultimodalWorkspace key="embedding" kind="embedding"/>}
  ]} destroyOnHidden/>;
}

const noPolling = () => false;
const pollRuns = (data: PaginatedResponse<ValidationRun>) => data.items.some(activeValidationRun);
function setSource(dataset: ValidationSet) {
  return dataset.source?.kind === 'snapshot' ? `冻结包 ${String(dataset.source.split || 'val/test')}（需核对独立性证据）` : '已确认标注快照（未验证独立）';
}

function DetectionValidationPage() {
  const [params]=useSearchParams();
  const contextId=params.get('project_id')||undefined;
  const [projects, setProjects] = useState<Project[]>([]);
  const [admin, setAdmin] = useState(false), [ready, setReady] = useState(false), [setupError, setSetupError] = useState(''), [setupRevision, setSetupRevision] = useState(0);
  const [setPage, setSetPage] = useState(1), [setPageSize, setSetPageSize] = useState(20), [setRevision, setSetRevision] = useState(0);
  const [runPage, setRunPage] = useState(1), [runPageSize, setRunPageSize] = useState(20), [runRevision, setRunRevision] = useState(0);
  const [filterSet, setFilterSet] = useState<ValidationSet>(), [creatingSet, setCreatingSet] = useState(false), [creatingRun, setCreatingRun] = useState<ValidationSet>();
  const [selectedRun, setSelectedRun] = useState<string>(), [confirmCancel, setConfirmCancel] = useState<ValidationRun>();
  const [confirmDelete,setConfirmDelete]=useState<ValidationRun>();
  const [setAction,setSetAction]=useState<{dataset:ValidationSet;action:'refresh'|'delete'}>();
  const [busyId, setBusyId] = useState(''), [actionError, setActionError] = useState('');
  const writing = useRef(false), mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    setReady(false); setAdmin(false); setSetupError('');
    (async () => {
      const user = await getCurrentUser();
      const all: Project[] = [];
      if (user.role === 'admin') {
        for (let page = 1; ; page++) {
          const result = await getProjects({ page, page_size: 100, task_type: 'detection' });
          if (!current) return;
          all.push(...result.items);
          if (all.length >= result.total) break;
          if (!result.items.length) throw Error('项目分页不完整');
        }
      }
      if (!current) return;
      setProjects(all.filter(p => p.task_type === 'detection'));
      setAdmin(user.role === 'admin'); setReady(true);
    })().catch(e => { if (current) setSetupError(validationError(e, '权限、项目或模型读取失败')); });
    return () => { current = false; };
  }, [setupRevision]);
  const loadSets = useCallback((signal: AbortSignal) => getValidationSets({ page: setPage, page_size: setPageSize }, signal), [setPage, setPageSize]);
  const sets = useAutomationFeed(`${setPage}:${setPageSize}`, loadSets, noPolling, setRevision);
  const loadRuns = useCallback((signal: AbortSignal) => getValidationRuns({ set_id: filterSet?.id, page: runPage, page_size: runPageSize }, signal), [filterSet?.id, runPage, runPageSize]);
  const runs = useAutomationFeed(`${filterSet?.id || ''}:${runPage}:${runPageSize}`, loadRuns, pollRuns, runRevision);
  const change = async (run: ValidationRun, action: 'cancel' | 'retry' | 'delete') => {
    if (!admin || writing.current) return;
    writing.current = true; setBusyId(run.id); setActionError('');
    try {
      if(action==='delete')await deleteValidationRun(run.id);
      else await changeValidationRun(run.id, action);
      if (mounted.current) {
        setConfirmCancel(undefined);setConfirmDelete(undefined);setRunRevision(n => n + 1);
        if(action==='delete'){
          setSelectedRun(current=>current===run.id?undefined:current);
          if(runPage>1&&runs.data?.items.length===1)setRunPage(page=>page-1);
        }
      }
    } catch (e) { if (mounted.current) setActionError(validationError(e, '任务操作失败，请刷新后重试')); }
    finally { writing.current = false; if (mounted.current) setBusyId(''); }
  };
  const changeSet=async()=>{
    if(!admin||!setAction||writing.current)return;
    const {dataset,action}=setAction;writing.current=true;setBusyId(dataset.id);setActionError('');
    try{
      if(action==='refresh'){
        const updated=await refreshValidationSet(dataset.id);
        if(mounted.current){setFilterSet(updated);setRunPage(1);setSetPage(1);setSelectedRun(undefined);}
      }else{
        await deleteValidationSet(dataset.id);
        if(mounted.current){if(filterSet?.id===dataset.id){setFilterSet(undefined);setRunPage(1);}setSelectedRun(undefined);
          if(setPage>1&&sets.data?.items.length===1)setSetPage(page=>page-1);}
      }
      if(mounted.current){setSetAction(undefined);setSetRevision(n=>n+1);setRunRevision(n=>n+1);}
    }catch(cause){if(mounted.current)setActionError(validationError(cause,action==='refresh'?'更新标注失败':'删除验证集失败'));}
    finally{writing.current=false;if(mounted.current)setBusyId('');}
  };
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {projects.some(p=>p.id===contextId)&&<Alert type="info" title={`验证项目：${projects.find(p=>p.id===contextId)?.project_name}`} description="创建验证集时自动预选本项目；已有验证集与任务仍完整展示。" action={<Link to={`/projects/${contextId}/deliver`}>返回项目</Link>}/>}
    <Alert type="info" showIcon title="固定标注真值，直接验证训练好的检测权重" description="人工审核或管理员信任导入的YOLO标注均可作为真值。无需部署推理服务；结果不修改标注，仍需核对验证数据独立于训练数据。" />
    {ready && !admin && <Alert type="info" title="只读模式：创建、取消、重试和删除仅管理员可操作。" />}
    {setupError && <Alert type="error" title={setupError} action={<Button onClick={() => setSetupRevision(n => n + 1)}>重试初始化</Button>} />}
    <Card title="验证集列表" styles={{ body: { minWidth: 0 } }}>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button aria-label="刷新验证集" loading={sets.loading} onClick={() => setSetRevision(n => n + 1)}>刷新列表</Button>
        {admin && <Button type="primary" disabled={!projects.length} onClick={() => setCreatingSet(true)}>创建验证集</Button>}
      </Space>
      {admin && !projects.length && <Alert type="info" title="暂无检测项目，请先创建项目并审核标注或导入已信任的YOLO标注；历史验证集仍可查看和运行。" />}
      {sets.error && <Alert type="error" title="验证集读取失败，请重试；当前列表可能已过期。" />}
      <Table rowKey="id" loading={sets.loading} dataSource={sets.data?.items || []} scroll={{ x: 1000 }} locale={{ emptyText: '暂无验证集，管理员可先创建验证集' }} columns={[
        { title: '名称', dataIndex: 'name', width: 180, ellipsis: true },
        { title: '项目', dataIndex: 'project_id', width: 160, render: id => id ? <Link to={`/annotations?project_id=${id}`}>{projects.find(p => p.id === id)?.project_name || id}</Link> : '原项目已删除' },
        { title: '图片数', dataIndex: 'total', width: 90 }, { title: '类别', dataIndex: 'labels', width: 150, render: labels => labels.join('、') || '—' },
        { title: '来源', width: 230, render: (_, dataset) => setSource(dataset) },
        { title: '创建时间', dataIndex: 'created_at', width: 200 },
        { title: '操作', width: 380, render: (_, dataset) => <Space wrap>
          <Button onClick={() => { setFilterSet(dataset); setRunPage(1); setSelectedRun(undefined); }}>查看任务</Button>
          {admin && <Button onClick={() => setCreatingRun(dataset)} disabled={dataset.total === 0}>验证权重</Button>}
          {admin&&<Button disabled={!!busyId||!dataset.project_id} onClick={()=>{setActionError('');setSetAction({dataset,action:'refresh'});}}>更新标注</Button>}
          {admin&&<Button danger disabled={!!busyId} onClick={()=>{setActionError('');setSetAction({dataset,action:'delete'});}}>删除验证集</Button>}
        </Space> },
      ]} pagination={{ current: setPage, pageSize: setPageSize, total: sets.data?.total || 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100], onChange: (page, size) => { setSetPage(size === setPageSize ? page : 1); setSetPageSize(size); } }} />
    </Card>
    <Card title="验证任务" styles={{ body: { minWidth: 0 } }}>
      <Space wrap style={{ marginBottom: 16 }}><Button aria-label="刷新任务" loading={runs.loading} onClick={() => setRunRevision(n => n + 1)}>刷新任务</Button>
        {filterSet && <><Typography.Text>验证集：{filterSet.name}</Typography.Text><Button onClick={() => { setFilterSet(undefined); setRunPage(1); setSelectedRun(undefined); }}>显示全部任务</Button></>}
      </Space>
      {runs.error && <Alert type="error" title="任务读取失败，请重试；当前列表可能已过期。" />}
      {actionError && <Alert type="error" title={actionError} />}
      <Table rowKey="id" loading={runs.loading} dataSource={runs.data?.items || []} scroll={{ x: 1100 }} locale={{ emptyText: '暂无验证任务，请从验证集选择运行模型' }} columns={[
        { title: '任务', dataIndex: 'name', width: 180, render: (name, run) => name || run.id },
        { title: '模型 / 权重版本', width: 180, render: (_, run) => run.model.source === 'checkpoint' ? `${run.model.name} · ${run.model.weight_sha256?.slice(0, 8)}` : `${run.model.name} · r${run.model.revision}` },
        { title: '状态', dataIndex: 'status', width: 110, render: status => <Tag color={status === 'failed' ? 'error' : status === 'completed' ? 'success' : 'default'}>{validationStatusLabels[status] || status}</Tag> },
        { title: '已处理 / 总量', width: 170, render: (_, run) => <>{run.processed} / {run.total}
          {run.model.source==='checkpoint'&&activeValidationRun(run)&&run.processed===0&&<Typography.Paragraph type="secondary" style={{marginBottom:0}}>准备、上传或批量推理期间暂为 0</Typography.Paragraph>}
        </> },
        { title: '失败', dataIndex: 'failed_count', width: 80 },
        { title: '错误', dataIndex: 'error', width: 230, render: value => value || '—' },
        { title: '操作', width: 230, render: (_, run) => <Space wrap>
          <Button onClick={() => setSelectedRun(run.id)}>查看结果</Button>
          {admin && (activeValidationRun(run) || run.remote_active) && <Button danger disabled={!!busyId} onClick={() => { setActionError(''); setConfirmCancel(run); }}>{run.remote_active && !activeValidationRun(run) ? '确认停止验证' : '取消任务'}</Button>}
          {admin && !activeValidationRun(run) && (run.failed_count > 0 || run.status === 'failed' || run.status === 'canceled') && <Button aria-label="重试失败项" loading={busyId === run.id} disabled={!!busyId || run.remote_active} onClick={() => { void change(run, 'retry'); }}>重试失败项</Button>}
          {admin&&<Button danger disabled={!!busyId} onClick={()=>{setActionError('');setConfirmDelete(run);}}>删除任务</Button>}
        </Space> },
      ]} pagination={{ current: runPage, pageSize: runPageSize, total: runs.data?.total || 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100], onChange: (page, size) => { setRunPage(size === runPageSize ? page : 1); setRunPageSize(size); } }} />
    </Card>
    {selectedRun && <ValidationResults key={selectedRun} runId={selectedRun} revision={runRevision} onClose={() => setSelectedRun(undefined)} />}
    {creatingSet && admin && <CreateValidationSet projects={projects} projectId={projects.some(p=>p.id===contextId)?contextId:undefined} onClose={() => setCreatingSet(false)} onCreated={() => { setCreatingSet(false); setSetPage(1); setSetRevision(n => n + 1); }} />}
    {creatingRun && admin && <CreateValidationRun dataset={creatingRun} onClose={() => setCreatingRun(undefined)} onCreated={() => { setCreatingRun(undefined); setFilterSet(undefined); setRunPage(1); setRunRevision(n => n + 1); }} />}
    {confirmCancel && <Modal open title="取消验证任务？" okText="确认取消任务" cancelText="继续执行" okButtonProps={{ danger: true }} confirmLoading={!!busyId} closable={!busyId} mask={{ closable: !busyId }} onCancel={() => { if (!writing.current) setConfirmCancel(undefined); }} onOk={() => change(confirmCancel, 'cancel')}>
      <Typography.Paragraph>取消“{confirmCancel.name || confirmCancel.id}”的待处理样本。已有结果保留，报告将标记覆盖不完整。</Typography.Paragraph>
      {actionError && <Alert type="error" title={actionError} />}
    </Modal>}
    {confirmDelete&&<Modal open title="删除验证任务？" okText="确认删除任务" cancelText="保留任务" okButtonProps={{danger:true}} confirmLoading={!!busyId} closable={!busyId} mask={{closable:!busyId}} onCancel={()=>{if(!writing.current)setConfirmDelete(undefined);}} onOk={()=>change(confirmDelete,'delete')}>
      <Typography.Paragraph>删除“{confirmDelete.name||confirmDelete.id}”及其逐图评测结果，删除后无法恢复。验证集、原图、标注和训练权重保留。</Typography.Paragraph>
      {(activeValidationRun(confirmDelete)||confirmDelete.remote_active)&&<Typography.Paragraph>将先取消任务并确认远端验证停止；停止失败时保留任务，请重试。</Typography.Paragraph>}
      {actionError&&<Alert type="error" title={actionError}/>}
    </Modal>}
    {setAction&&<Modal open title={setAction.action==='refresh'?'更新验证集标注？':'删除验证集？'} okText={setAction.action==='refresh'?'生成新版验证集':'确认删除验证集'} cancelText="取消" okButtonProps={{danger:setAction.action==='delete'}} confirmLoading={!!busyId} closable={!busyId} mask={{closable:!busyId}} onCancel={()=>{if(!writing.current)setSetAction(undefined);}} onOk={()=>{void changeSet();}}>
      {setAction.action==='refresh'?<Typography.Paragraph>使用“{setAction.dataset.name}”现有这批图片在项目中的最新已审核标注，生成新版验证集，并更新类别列表。修改后尚未审核的标注请先审核。原验证集和历史结果保留，不自动重跑推理；原冻结包的独立性声明不会继承。</Typography.Paragraph>:
        <Typography.Paragraph>删除“{setAction.dataset.name}”及其全部验证任务和逐图结果，删除后无法恢复。项目、原图、标注、训练权重保留。有任务待执行、运行中或远端未确认停止时，会拒绝删除，请先取消任务。</Typography.Paragraph>}
      {actionError&&<Alert type="error" title={actionError}/>}
    </Modal>}
  </Space>;
}
