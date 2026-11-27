import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, InputNumber, Select, Space, Table, Tag } from 'antd';
import { ReloadOutlined, UnorderedListOutlined } from '@ant-design/icons';
import ImageIngestSettings from '../src/components/ImageIngestSettings';
import { getImageCollections } from '../src/api/images';
import { apiFetch, setDestination } from './network';
import type { ImageCollection } from '../src/api/images';
import type { Destination } from './network';
import { allProjects, taskKinds } from './managementApi';
import type {Project} from '../src/api/projects';
import CreateProjectDialog from './components/CreateProjectDialog';
import {openProjectAutomation,withCreatedProjects} from './projectActions';

type Task = { filtered_count?: number; task_id: string; status: string; kind: string; message?: string; project_id?:string };
const statuses: Record<string, { label: string; color: string }> = {
  pending: { label: '等待中', color: 'default' }, running: { label: '运行中', color: 'processing' },
  processing: { label: '处理中', color: 'processing' }, completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' }, cancelled: { label: '已取消', color: 'default' },
  interrupted: { label: '已中断', color: 'warning' },
};
const kinds: Record<string, string> = { detection: '检测下载', ledger: '台账下载', 'smart-search': '智搜下载', tool: '工具任务', 'ai-image': 'AI 生图', watch: '监听任务', selection: '图片下载', material: '素材' };

export default function IntegrationBar() {
  const [collections,setCollections]=useState<ImageCollection[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const createdProjects=useRef<Project[]>([]);
  const [choice, setChoice] = useState<Destination>(() => {
    const projectId = new URLSearchParams(location.search).get('project_id') || '';
    return { purpose: projectId ? 'annotation' : 'download', projectId };
  });
  useEffect(() => { setDestination(choice); }, [choice]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [showTasks, setShowTasks] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState('');
  const [creating,setCreating]=useState(false);
  const refresh = async () => {
    setLoading(true);
    try {
      const response = await apiFetch('/api/smarteye/tasks');
      if (!response.ok) throw new Error('任务列表读取失败，请重试');
      const data = await response.json();
      setTasks(data.data || []);
    } catch { setError('任务列表读取失败，请重试'); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    let active = true;
    getImageCollections().then(items=>{if(active)setCollections(items);}).catch(()=>{if(active)setError('图片集读取失败，请刷新页面');});
    allProjects().then(items => { if (active) setProjects(withCreatedProjects(items,createdProjects.current)); }).catch(() => { if (active) setError('项目读取失败，请刷新页面'); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!showTasks) return;
    void refresh();
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, [showTasks]);
  const change = (value: Destination) => { setChoice(value); setDestination(value); };
  const selectedProject=projects.find(project=>project.id===choice.projectId);
  const created=(project:Project)=>{
    createdProjects.current=[project,...createdProjects.current];
    setProjects(previous=>[project,...previous.filter(item=>item.id!==project.id)]);
    change({...choice,purpose:'annotation',projectId:project.id});setCreating(false);
    window.dispatchEvent(new CustomEvent('smarteye:projects-updated',{detail:project}));
  };
  const openAutomation=()=>{
    if(choice.purpose!=='annotation'||selectedProject?.task_type!=='detection')return;
    openProjectAutomation(selectedProject.id);
  };
  const action = async (task: Task, operation: 'cancel' | 'retry') => {
    setActing(task.task_id); setError('');
    try {
      const response = await apiFetch(`/api/smarteye/tasks/${task.task_id}/${operation}`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok || data.code !== 0) { setError(data.message || '操作失败'); return; }
      await refresh();
    } catch { setError('任务操作失败，请重试'); }
    finally { setActing(''); }
  };
  return <section className="smarteye-destination" aria-label="拉取数据用途">
    <div className="smarteye-destination-fields">
      <div>
        <label htmlFor="smarteye-purpose">拉取用途</label>
        <Select id="smarteye-purpose" aria-label="数据拉取用途" value={choice.purpose}
          onChange={purpose => change({ ...choice, purpose })}
          options={[{ value: 'download', label: '下载并存入图片库' }, { value: 'annotation', label: '直接用于标注' }]} />
      </div>
      {choice.purpose === 'annotation' && <div>
        <label htmlFor="smarteye-project">标注项目</label>
        <Select id="smarteye-project" aria-label="标注项目" placeholder="选择项目" showSearch
          optionFilterProp="label" value={choice.projectId || undefined}
          onChange={projectId => change({ ...choice, projectId })}
          options={projects.map(p => ({ value: p.id, label: p.project_name }))} />
      </div>}
      <div>
        <label htmlFor="smarteye-collection">目标图片集</label>
        <Select id="smarteye-collection" aria-label="拉取目标图片集" allowClear showSearch optionFilterProp="label" placeholder="按平台名自动归类" value={choice.collectionId}
          onChange={collectionId=>change({...choice,collectionId,minLongEdge:undefined})} options={collections.map(group=>({value:group.id,label:group.name}))}/>
      </div>
      <div>
        <label htmlFor="smarteye-minimum">最小长边（px）</label>
        <InputNumber id="smarteye-minimum" aria-label="拉取长边阈值" title="0 表示不限长边" min={0} max={32768} precision={0} placeholder="按图片集规则" value={choice.minLongEdge} onChange={value=>change({...choice,minLongEdge:value??undefined})}/>
      </div>
    </div>
    <div className="smarteye-destination-actions">
      <Button onClick={()=>setCreating(true)}>新建项目</Button>
      <Button disabled={choice.purpose!=='annotation'||selectedProject?.task_type!=='detection'} title="请选择检测项目后进行预标注与AI审核" onClick={openAutomation}>预标注与AI审核</Button>
      <ImageIngestSettings collectionId={choice.collectionId} collectionName={collections.find(group=>group.id===choice.collectionId)?.name} onSaved={()=>{void getImageCollections().then(setCollections).catch(()=>setError('图片集读取失败，请刷新页面'));}}/>
      <Button icon={<UnorderedListOutlined />} onClick={() => setShowTasks(true)}>任务记录</Button>
    </div>
    {choice.purpose === 'annotation' && !choice.projectId && <Alert type="warning" showIcon title="请选择标注项目后再启动拉取任务" />}
    {error && !showTasks && <Alert type="error" showIcon title={error} />}
    {creating&&<CreateProjectDialog onClose={()=>setCreating(false)} onCreated={created}/>}
    <Drawer title="SmartEye 任务记录" open={showTasks} onClose={() => setShowTasks(false)} size={720}
      extra={<Button aria-label="刷新任务记录" icon={<ReloadOutlined />} onClick={refresh} />}>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
      <Table rowKey="task_id" dataSource={tasks} loading={loading} size="small" scroll={{ x: 560 }}
        locale={{ emptyText: <Empty description="暂无拉取任务" /> }}
        pagination={{ pageSize: 8, showTotal: total => `共 ${total} 个任务` }}
        columns={[
          { title: '任务类型', dataIndex: 'kind', width: 110, render: (kind: string) => taskKinds[kind] || kinds[kind] || kind },
          { title: '关联项目', dataIndex: 'project_id', render: (id?:string) => id ? projects.find(project=>project.id===id)?.project_name || id : '仅入图片库' },
          { title: '状态', dataIndex: 'status', width: 100, render: (status: string) => <Tag color={statuses[status]?.color}>{statuses[status]?.label || status}</Tag> },
          { title: '任务信息', render: (_: unknown, task: Task) => <div><span>{task.message || '—'}</span>{!!task.filtered_count&&<div>按分辨率规则跳过 {task.filtered_count} 张</div>}<div className="smarteye-task-id">{task.task_id}</div></div> },
          { title: '操作', width: 90, render: (_: unknown, task: Task) => <Space>
            {['pending', 'running', 'processing'].includes(task.status) && <Button aria-label="取消任务" size="small" loading={acting === task.task_id} disabled={!!acting} onClick={() => action(task, 'cancel')}>取消</Button>}
            {['failed', 'cancelled', 'interrupted'].includes(task.status) && ['detection', 'ledger', 'smart-search', 'tool'].includes(task.kind) && <Button aria-label="重试任务" size="small" loading={acting === task.task_id} disabled={!!acting} onClick={() => action(task, 'retry')}>重试</Button>}
          </Space> },
        ]} />
    </Drawer>
  </section>;
}
