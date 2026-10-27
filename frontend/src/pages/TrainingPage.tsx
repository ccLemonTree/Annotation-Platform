import { useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Checkbox, Col, Collapse, Descriptions, Drawer, Form, Input, InputNumber, Modal, Row, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography } from 'antd';
import { getGpuResources } from '../api/gpuResources';
import type { GpuResource, GpuServer } from '../api/gpuResources';
import { getProjects } from '../api/projects';
import type { Project } from '../api/projects';
import { createTrainingRun, updateTrainingRun, deleteTrainingRun, downloadTrainingArtifact, getTrainingArtifacts, getTrainingCatalog, getTrainingDefaults, getTrainingLogs, getTrainingRun, getTrainingRuns, getTrainingServers, getTrainingSnapshots, trainingAction } from '../api/training';
import TrainingLogs from '../components/TrainingLogs';
import TrainingStartup from '../components/TrainingStartup';
import TrainingAnalysis from '../components/TrainingAnalysis';
import TrainingPlatformData from '../components/TrainingPlatformData';
import ModelOutputForm from '../components/ModelOutputForm';
import JsonView from '../components/JsonView';
import type { ParameterField, TrainingArtifact, TrainingCheck, TrainingInput, TrainingRun, TrainingSnapshot, TrainingTemplate } from '../api/training';

const names: Record<string, string> = { draft: '待启动', starting: '准备中', running: '训练中', stopping: '停止中', unknown: '状态待核对', completed: '已完成', failed: '失败', canceled: '已取消' };
const labels: Record<string, string> = {
  epochs: '训练轮数', batch: '总批次大小', patience: '早停耐心轮数', save_period: '保存间隔（轮）', device: '容器内GPU编号', seed: '随机种子',
  batch_size: '每卡批次大小', grad_accum_steps: '梯度累计步数', lr: '学习率', devices: '训练设备数',
  task_type: '任务类型', loss_type: '损失函数', tuner_type: '微调方式', lora_rank: 'LoRA秩', lora_alpha: 'LoRA缩放',
  torch_dtype: '计算精度', num_train_epochs: '训练轮数', per_device_train_batch_size: '每卡批次大小', gradient_accumulation_steps: '梯度累计步数',
  learning_rate: '学习率', max_length: '最大序列长度', lazy_tokenize: '延迟编码', dataloader_num_workers: '数据加载进程数',
  logging_steps: '日志间隔（步）', save_steps: '保存间隔（步）', save_total_limit: '最多保留检查点数', eval_strategy: '评估策略',
  val_dataset: '服务器验证JSONL路径', dataloader_drop_last: '丢弃不足批次', freeze_vit: '冻结视觉编码器', freeze_aligner: '冻结对齐层',
  steps: '总训练步数', backbone_freeze: '冻结DINOv3骨干', backbone_lr_factor: '骨干学习率比例', warmup_steps: '学习率预热步数', precision: '计算精度', num_workers: '数据加载进程数',
};
const envLabels: Record<string, string> = { MAX_PIXELS: '图像像素上限', IMAGE_MAX_TOKEN_NUM: '图像token上限（可选）', NPROC_PER_NODE: '单机进程数', CUDA_VISIBLE_DEVICES: '容器内可见GPU',
  INFONCE_USE_BATCH: '使用批内负样本', INFONCE_HARD_NEGATIVES: '困难负样本数', TOKENIZERS_PARALLELISM: '分词器并行', PYTORCH_CUDA_ALLOC_CONF: 'CUDA内存分配配置' };
const active = ['starting', 'running', 'stopping', 'unknown'];
function errorText(error: unknown): string {
  const e = error as { response?: { data?: { detail?: unknown; message?: string } }; message?: string };
  const detail = e.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(item => item && typeof item.msg === 'string' ? item.msg : '').filter(Boolean);
    if (messages.length) return messages.join('；');
  }
  return e.response?.data?.message || e.message || '操作失败，请重试';
}
const outputKinds=['model_export','model_merge'];
const outputSources=['yolo26','rfdetr_medium','rfdetr_2xlarge','swift_sft','wemm'];
const outputNames:Record<string,string>={model_export:'模型导出',model_merge:'LoRA合并'};
function status(run: TrainingRun) { return <Tag color={run.status === 'completed' ? 'success' : run.status === 'failed' || run.status === 'unknown' ? 'error' : active.includes(run.status) ? 'processing' : 'default'}>{run.status==='running'&&outputKinds.includes(run.kind)?'执行中':names[run.status] || run.status}</Tag>; }
function ParameterInput({ name, field, value, checked, onChange, id }: { name: string; field: ParameterField; value?: unknown; checked?: boolean; onChange?: (value: unknown) => void; id?: string }) {
  const schema = field.anyOf?.find(v => v.type !== 'null') || field;
  const control = { id, onChange };
  if (schema.const !== undefined) return <Select {...control} value={value} options={[{ value: schema.const, label: String(schema.const) }]} />;
  if (name === 'devices') return <Select {...control} value={value as string | number} options={[{ value: 'auto', label: '自动使用已分配GPU' }, ...Array.from({ length: 16 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))]} />;
  if (schema.enum) return <Select {...control} value={value} allowClear options={schema.enum.map(option => ({ value: option, label: String(option) }))} />;
  if (schema.type === 'boolean') return <Switch {...control} checked={checked} />;
  if (schema.type === 'integer' || schema.type === 'number') return <InputNumber {...control} value={value as number} style={{ width: '100%' }} min={schema.minimum ?? schema.exclusiveMinimum} max={schema.maximum} step={schema.type === 'integer' ? 1 : name.includes('lr') || name.includes('rate') ? 0.00001 : 0.1} />;
  return <Input {...control} value={value as string} placeholder={name === 'device' ? 'auto 或 0,1' : undefined} />;
}

export default function TrainingPage() {
  const [params]=useSearchParams();
  const contextId=params.get('project_id');
  const [form] = Form.useForm();
  const [admin] = useState(() => { try { return JSON.parse(localStorage.getItem('user') || 'null')?.role === 'admin'; } catch { return false; } });
  const [catalog, setCatalog] = useState<Record<string, TrainingTemplate>>({}), [servers, setServers] = useState<GpuServer[]>([]), [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<TrainingRun[]>([]), [total, setTotal] = useState(0), [page, setPage] = useState(1), [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [open, setOpen] = useState(false), [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [kind, setKind] = useState('yolo26'), [serverId, setServerId] = useState('default'), [gpus, setGpus] = useState<GpuResource[]>([]), [gpuLoading, setGpuLoading] = useState(false), [gpuError, setGpuError] = useState('');
  const [source, setSource] = useState('remote'), [snapshots, setSnapshots] = useState<TrainingSnapshot[]>([]), [snapshotLoading, setSnapshotLoading] = useState(false);
  const [selected, setSelected] = useState<TrainingRun>(), [detailTab, setDetailTab] = useState('overview'), [busy, setBusy] = useState(''), [detailError, setDetailError] = useState('');
  const selectedId=selected?.id,selectedStatus=selected?.status;
  const [logs, setLogs] = useState(''), [files, setFiles] = useState<TrainingArtifact[]>([]), [truncated, setTruncated] = useState(false);
  const [confirm, setConfirm] = useState<'start' | 'stop' | 'delete'>();
  const [datasetLabels, setDatasetLabels] = useState<string[]>([]);
  const [editing, setEditing] = useState<string>();
  const [outputPath,setOutputPath]=useState<string>();
  const swift = kind === 'swift_sft' || kind === 'wemm';
  const dinov3 = kind === 'dinov3_vits16_ltdetr';
  const taskType=Form.useWatch(['parameters','task_type'],form),lossType=Form.useWatch(['parameters','loss_type'],form);
  const datasetFormat=taskType==='embedding'?(lossType||'infonce'):'swift_sft';
  const context=projects.find(project=>project.id===contextId);
  useEffect(() => {
    const controller = new AbortController();
    getTrainingCatalog(controller.signal).then(setCatalog).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
    if (admin) {
      getTrainingServers(controller.signal).then(setServers).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
      (async()=>{const rows:Project[]=[];
        for(let page=1;!controller.signal.aborted;page++){
          const result=await getProjects({page,page_size:100});rows.push(...result.items);
          if(rows.length>=result.total||!result.items.length)break;
        }
        if(!controller.signal.aborted)setProjects(rows);
      })().catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
    }
    return () => controller.abort();
  }, [admin]);
  useEffect(() => {
    const controller = new AbortController(); let pending = false;
    const load = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const result = await getTrainingRuns(page, controller.signal);
        if (!controller.signal.aborted) { setRuns(result.items); setTotal(result.total); setError(''); }
      } catch (cause) { if (!controller.signal.aborted) setError(errorText(cause)); }
      finally { pending = false; if (!controller.signal.aborted) setLoading(false); }
    };
    void load(); const timer = setInterval(() => { void load(); }, 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [page, reload]);
  useEffect(()=>{
    if(!selectedId||!selectedStatus||!active.includes(selectedStatus))return;
    const id=selectedId;let alive=true,pending=false;
    const load=async()=>{
      if(pending||document.hidden)return;
      pending=true;
      try{const run=await getTrainingRun(id);if(alive)setSelected(previous=>previous?.id===id?run:previous);}
      catch(cause){if(alive)setDetailError(errorText(cause));}
      finally{pending=false;}
    };
    void load();const timer=setInterval(()=>void load(),selectedStatus==='starting'?3000:15000);
    return()=>{alive=false;clearInterval(timer);};
  },[selectedId,selectedStatus]);
  useEffect(() => {
    if (!open || !admin) return;
    const controller = new AbortController();
    getGpuResources(controller.signal, serverId).then(result => { if (!controller.signal.aborted) setGpus(result.gpus); })
      .catch(e => { if (!controller.signal.aborted) setGpuError(errorText(e)); })
      .finally(() => { if (!controller.signal.aborted) setGpuLoading(false); });
    return () => controller.abort();
  }, [open, serverId, admin]);
  useEffect(() => {
    if (!open || !admin) return;
    const controller = new AbortController();
    getTrainingDefaults(serverId, kind, controller.signal).then(values => {
      if (controller.signal.aborted) return;
      for (const key of ['model_path', 'output_root'] as const) if (!form.getFieldValue(key)) form.setFieldValue(key, values[key]);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)); });
    return () => controller.abort();
  }, [open, serverId, kind, admin, form]);
  const clearLabels = () => {
    setDatasetLabels([]); form.setFieldValue('selected_labels', null);
  };
  const changeKind = (value: string) => {
    setKind(value);setSource('remote');setSnapshots([]);
    clearLabels();
    form.setFieldsValue({ kind: value, model_path: undefined, image: catalog[value].image, parameters: catalog[value].defaults, environment: catalog[value].environment, accept_platform_model_license: false, snapshot_id: undefined,project_id:undefined });
  };
  const showCreate = () => {
    const initialKind=context?.task_type==='vector'?'wemm':context&&['caption','classification'].includes(context.task_type)?'swift_sft':'yolo26';
    setEditing(undefined);
    setSaveError('');
    setError(''); setKind(initialKind); setServerId(servers[0]?.id || 'default'); setSource(context?'snapshot':'remote'); setSnapshots([]);
    setGpuLoading(true); setGpuError(''); setGpus([]);
    clearLabels();
    form.resetFields(); form.setFieldsValue({ kind: initialKind, server_id: servers[0]?.id || 'default', image: catalog[initialKind]?.image,
      project_id:context?.id, output_subdir: 'train', shm_size_gb: 16, parameters: catalog[initialKind]?.defaults, environment: catalog[initialKind]?.environment, mounts: [], accept_platform_model_license: false });
    if(context&&initialKind==='yolo26'){
      setSnapshotLoading(true);
      getTrainingSnapshots(context.id).then(rows=>{if(form.getFieldValue('project_id')===context.id)setSnapshots(rows);}).catch(e=>setError(errorText(e))).finally(()=>setSnapshotLoading(false));
    }
    setOpen(true);
  };
  const showEdit = (run: TrainingRun, copy = false) => {
    setSaveError('');
    setEditing(copy ? undefined : run.id); setError(''); setKind(run.kind); setServerId(run.server.id); setSource(run.source.kind === 'snapshot' ? 'snapshot' : 'remote');
    setGpuLoading(true); setGpuError(''); setGpus([]); setSnapshots([]);
    setDatasetLabels(run.source.labels || []);
    form.resetFields();
    const values = structuredClone(run.config) as TrainingInput & { adapter_version?: number; runtime_dataset?: string };
    delete values.adapter_version; delete values.runtime_dataset;
    form.setFieldsValue({ ...values, name: copy ? `${run.name.slice(0,197)} 副本` : run.name, server_id: run.server.id, project_id: run.source.project_id });
    setOpen(true);
    if (run.source.project_id) {
      setSnapshotLoading(true);
      getTrainingSnapshots(run.source.project_id).then(setSnapshots).catch(cause => setError(errorText(cause))).finally(() => setSnapshotLoading(false));
    }
  };
  const save = async (values: TrainingInput) => {
    setSaving(true); setSaveError('');
    try {
      const defaults = catalog[kind].defaults;
      const parameters = Object.fromEntries(Object.keys(defaults).map(key => [key, values.parameters?.[key] ?? defaults[key]]));
      const input: TrainingInput = { ...values, parameters, environment: Object.fromEntries(Object.entries(values.environment || {}).filter(([key]) => key in catalog[kind].environment)),
        mounts: values.mounts || [], dataset_path: source === 'remote' ? values.dataset_path : '', snapshot_id: source === 'snapshot' ? values.snapshot_id : undefined,
        selected_labels: source === 'snapshot'&&!swift ? values.selected_labels ?? null : null };
      // Project selection is only a UI filter; the backend scopes the immutable snapshot itself.
      delete (input as TrainingInput & { project_id?: string }).project_id;
      const run = editing ? await updateTrainingRun(editing, input) : await createTrainingRun(input); setOpen(false); setSelected(run); setDetailTab('overview'); setDetailError(''); setReload(n => n + 1);
    }
    catch (cause) { setSaveError(errorText(cause)); }
    finally { setSaving(false); }
  };
  const inspect = async (run: TrainingRun) => { setSelected(run); setDetailTab('overview'); setDetailError(''); setLogs(''); setFiles([]);setOutputPath(undefined); };
  const action = async (value: string) => {
    if (!selected || busy) return;
    setBusy(value); setDetailError('');
    try {
      if(value==='delete'){
        await deleteTrainingRun(selected.id);setSelected(undefined);setConfirm(undefined);setPage(1);setReload(n=>n+1);return;
      }
      const result=await trainingAction(selected.id, value);
      if(value==='start'){
        setSelected(result as TrainingRun);setConfirm(undefined);setDetailTab('startup');setReload(n=>n+1);return;
      }
      setSelected(await getTrainingRun(selected.id)); setReload(n => n + 1);
      if (value === 'precheck') setDetailTab('checks');
      setConfirm(undefined);
    } catch (cause) {
      if(value==='start'){setConfirm(undefined);setDetailTab('startup');}
      setDetailError(value==='start'&&isAxiosError(cause)&&!cause.response?'启动提交结果尚未确认，请核对后台进度及运行状态，勿重复启动。':errorText(cause));
      try { setSelected(await getTrainingRun(selected.id)); } catch { /* Keep the current detail and the actionable error. */ }
    } finally { setBusy(''); }
  };
  const readOutput = async (tab: string) => {
    setDetailTab(tab); if (!selected || busy || !['logs', 'artifacts'].includes(tab)) return;
    setBusy(tab); setDetailError('');
    try {
      if (tab === 'logs') setLogs((await getTrainingLogs(selected.id)).text);
      else { const result = await getTrainingArtifacts(selected.id); setFiles(result.files); setTruncated(result.truncated); }
    } catch (cause) { setDetailError(errorText(cause)); } finally { setBusy(''); }
  };
  const download = async (file:TrainingArtifact) => {
    if(!selected||busy)return;
    setBusy('download');setDetailError('');
    try{await downloadTrainingArtifact(selected.id,file.path);}catch(cause){setDetailError(errorText(cause));}finally{setBusy('');}
  };
  const checkResult = selected?.inspection as TrainingCheck | undefined;
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {context&&<Alert type="info" title={`使用项目数据：${context.project_name}`} description="点击创建训练任务，自动预选本项目、平台数据来源和适用训练器。" action={<Link to={`/projects/${context.id}/deliver`}>返回项目导出与训练</Link>}/>}
    <Alert type="info" showIcon title="先保存配置，再预检查和启动" description="任务不会自动开始。每次训练使用独立输出目录，已有GPU计算进程会阻止启动；状态未知时保留资源占用，需核对或停止。" />
    {!admin && <Alert type="info" title="你可以查看任务、配置、日志与产物；创建和执行需要管理员权限。" />}
    {error && !open && <Alert type="error" showIcon title={error} />}
    <Card title="训练任务" extra={<Space wrap><Button onClick={() => setReload(n => n + 1)}>刷新列表</Button>{admin && <Button type="primary" disabled={!catalog.yolo26 || !servers.length} onClick={showCreate}>创建训练任务</Button>}</Space>}>
      <Table rowKey="id" dataSource={runs} loading={loading} scroll={{ x: 900 }} pagination={{ current: page, pageSize: 20, total, onChange: setPage, showSizeChanger: false }} columns={[
        { title: '任务', dataIndex: 'name', render: (_, run) => <Button type="link" onClick={() => { void inspect(run); }}>{run.name}</Button> },
        { title: '训练器', render: (_, run) => outputNames[run.kind] || catalog[run.kind]?.name || run.kind }, { title: '服务器', render: (_, run) => run.server.name },
        { title: 'GPU数', render: (_, run) => run.config.gpu_uuids.length }, { title: '状态', render: (_, run) => status(run) },
        { title: '创建时间', render: (_, run) => new Date(run.created_at).toLocaleString('zh-CN') },
        { title: '操作', render: (_, run) => <Space wrap><Button onClick={() => { void inspect(run); }}>查看详情</Button>{run.status!=='draft'&&!outputKinds.includes(run.kind)&&<Button onClick={()=>{void inspect(run);setDetailTab('analysis');}}>效果分析与对比</Button>}</Space> },
      ]} />
    </Card>
    <Modal open={open} title={editing ? '编辑训练任务配置' : '创建训练任务'} width={1000} footer={null} mask={{ closable: false }} destroyOnHidden onCancel={() => { if (!saving) setOpen(false); }} closable={!saving}>
      {error && <Alert type="error" title={error} showIcon style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical" onFinish={values => { void save(values); }} onFinishFailed={({ errorFields }) => setSaveError('配置未保存：' + errorFields.flatMap(field => field.errors).join('；'))} scrollToFirstError={{ block: 'center', focus: true }} disabled={saving} onValuesChange={changed=>{if(changed.parameters&&('task_type' in changed.parameters||'loss_type' in changed.parameters))form.setFieldValue('snapshot_id',undefined);}}>
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请填写任务名称' }]}><Input maxLength={200} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="kind" label="训练器" rules={[{ required: true }]}><Select onChange={changeKind} options={Object.entries(catalog).map(([value, template]) => ({ value, label: template.name }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="server_id" label="训练服务器" rules={[{ required: true }]}><Select onChange={id => { setServerId(id); setGpuLoading(true); setGpuError(''); setGpus([]); clearLabels(); form.setFieldsValue({ gpu_uuids: [], model_path: undefined, output_root: undefined }); }} options={servers.map(s => ({ value: s.id, label: s.name, disabled: !s.enabled }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="gpu_uuids" label="物理GPU（明确分配）" rules={[{ required: true, message: '请选择至少一张物理GPU' }]}><Select mode="multiple" loading={gpuLoading} placeholder="选择GPU；启动前再次检查占用" options={gpus.map(g => ({ value: g.uuid, label: `${g.index} · ${g.name} · 空闲${Math.round((g.memory_free_mib || 0) / 1024)}GiB` }))} /></Form.Item></Col>
        </Row>
        {gpuError && <Alert type="error" title={gpuError} showIcon />}
        <Form.Item label="数据来源"><Select aria-label="训练数据来源" value={source} onChange={value => { setSource(value); clearLabels(); form.setFieldsValue({ snapshot_id: undefined, dataset_path: undefined }); }} options={[{ value: 'remote', label: '服务器已有路径' }, { value: 'snapshot', label: swift?'平台标注数据（冻结版本）':'平台冻结导出集（检测标注）' }]} /></Form.Item>
        {dinov3 && <Alert type="info" showIcon title="DINOv3 电瓶车等目标的监督检测微调" description="使用 dinov3/vits16-ltdetr-coco 完整预训练检测权重；先完成框标注、审核和train/val划分。原始图片不能直接用于本训练器。默认1000步、总batch 4是小数据起点，需用独立验证图片评估；不使用test集训练或选模型。" style={{ marginBottom: 16 }} />}
        {swift && source==='remote' && <Alert type="info" title="Swift/WeMM使用已有JSONL；图片等引用路径通过下面的挂载配置提供。" style={{ marginBottom: 16 }} />}
        {source==='snapshot'&&swift?<TrainingPlatformData key={datasetFormat} form={form} projects={projects} format={datasetFormat}/>:source === 'snapshot' ? <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="project_id" label="冻结导出所属项目" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={projects.map(p => ({ value: p.id, label: p.project_name }))} onChange={async id => {
            clearLabels();
            form.setFieldValue('snapshot_id', undefined); setSnapshots([]); setSnapshotLoading(true);
            try { const result = await getTrainingSnapshots(id); if (form.getFieldValue('project_id') === id) setSnapshots(result); }
            catch (cause) { setError(errorText(cause)); } finally { setSnapshotLoading(false); }
          }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="snapshot_id" label="冻结版本" rules={[{ required: true }]} extra="先在项目导出中冻结已审核的train/val划分；不会重新导出。"><Select loading={snapshotLoading} onChange={id => { clearLabels(); const available = snapshots.find(s => s.id === id)?.manifest.labels || []; setDatasetLabels(available); form.setFieldValue('selected_labels', available.length ? available : null); }} options={snapshots.filter(s => kind !== 'yolo26' || s.format === 'yolo').map(s => ({ value: s.id, label: `${s.format.toUpperCase()} · ${new Date(s.created_at).toLocaleString('zh-CN')} · ${s.id.slice(0, 8)}` }))} /></Form.Item></Col>
        </Row> : <Form.Item name="dataset_path" label={swift ? '服务器训练JSONL绝对路径' : dinov3 ? '服务器YOLO配置文件或COCO数据集目录' : kind === 'yolo26' ? '服务器已有YAML配置绝对路径' : '服务器RF-DETR数据集目录或data.yaml/data.yml路径'} rules={[{ required: true }]} extra={swift ? undefined : dinov3 ? 'YOLO配置需包含names、train、val；COCO使用train/valid（或val）目录内的_annotations.coco.json。保留原数据，仅将train/val交给Lightly。' : '直接沿用服务器配置、标签编号和数据划分，不重新生成或过滤。RF-DETR文件名须为data.yaml/data.yml；配置中的容器路径请通过附加挂载保持一致。'}><Input onChange={clearLabels} placeholder="按所选服务器填写实际路径" /></Form.Item>}
        {!swift && source === 'snapshot' && <>
          <Alert type="info" title="平台自动生成训练配置" description="按所选冻结版本、训练器和标签生成配置及类别映射，无需手写YAML；保留原train/val/test划分。" style={{ marginBottom: 16 }} />
          <Form.Item name="selected_labels" label="训练标签" rules={datasetLabels.length ? [{ required: true, type: 'array', min: 1, message: '至少选择一个训练标签' }] : []} extra="默认训练全部标签；按原顺序重新编号。未选类别的框被移除，图片保留为背景样本。">
            <Select mode="multiple" disabled={!datasetLabels.length} placeholder="选择冻结版本后可选择训练标签" options={datasetLabels.map(label => ({ value: label, label }))} />
          </Form.Item>
        </>}
        <Form.Item name="model_path" label={dinov3 ? '服务器DINOv3完整检测权重路径' : '服务器基础权重文件或模型目录'} rules={[{ required: true }]} extra={dinov3 ? '填写已准备好的dinov3/vits16-ltdetr-coco完整检测.pt；不能填模型名称或仅骨干权重。容器禁网，不自动下载；预检查用CPU加载并核对型号。' : '保存任务后按服务器和训练器记住路径，下次自动填入，可修改。' + (kind === 'rfdetr_medium' ? '必须使用与Medium兼容的权重。' : '使用已有权重，不自动下载。')}><Input placeholder="首次填写所选服务器上的实际路径" /></Form.Item>
        {kind === 'rfdetr_2xlarge' && <Form.Item name="accept_platform_model_license" valuePropName="checked"><Checkbox>我已阅读并确认适用的 <a href="https://rfdetr.roboflow.com/latest/reference/2xlarge/" target="_blank" rel="noreferrer">Platform Model License</a></Checkbox></Form.Item>}
        <Form.Item name="image" label="训练镜像" rules={[{ required: true }]} extra="需要已在服务器就绪；平台不会重复拉取镜像。"><Input /></Form.Item>
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="output_root" label="服务器输出根目录" rules={[{ required: true }]} extra="默认按北京时间生成，可修改；不存在时自动创建，每任务再增加独立UUID目录。"><Input placeholder="/ai/chen/training/runs/年月日-时分秒" /></Form.Item></Col>
          <Col xs={12} md={6}><Form.Item name="output_subdir" label="训练产物子目录" rules={[{ required: true }]}><Input /></Form.Item></Col>
          <Col xs={12} md={6}><Form.Item name="shm_size_gb" label="共享内存（GiB）" rules={[{ required: true }]} extra="容器/dev/shm上限，供数据加载进程共享数据；占用主机内存，不是显存，也不预先占满。"><InputNumber min={1} max={1024} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Typography.Title level={5}>训练参数</Typography.Title>
        <Row gutter={16}>{Object.entries(catalog[kind]?.schema.properties || {}).map(([key, field]) => {
          const schema = field.anyOf?.find(v => v.type !== 'null') || field;
          return <Col xs={24} sm={12} md={8} key={kind + key}><Form.Item name={['parameters', key]} label={dinov3 && key === 'batch_size' ? '总批次大小（全部GPU）' : labels[key] || key} extra={key} valuePropName={schema.type === 'boolean' ? 'checked' : 'value'}><ParameterInput name={key} field={field} /></Form.Item></Col>;
        })}</Row>
        <Collapse style={{ marginBottom: 20 }} items={[
          { key: 'env', label: '环境变量与分布式参数', children: <><Alert type="info" title="auto随所选卡数生成；容器内GPU从0编号。MAX_PIXELS与IMAGE_MAX_TOKEN_NUM是否生效取决于镜像中的处理器。" style={{ marginBottom: 16 }} /><Row gutter={16}>{Object.keys(catalog[kind]?.environment || {}).map(key => <Col xs={24} md={12} key={key}><Form.Item name={['environment', key]} label={envLabels[key] || key} extra={key}><Input /></Form.Item></Col>)}</Row></> },
          { key: 'mounts', label: '附加数据挂载', children: <><Typography.Paragraph>训练数据所在目录、基础权重目录和输出目录自动挂载。JSONL引用其他图片目录时，在此补充；建议只读。</Typography.Paragraph><Form.List name="mounts">{(fields, { add, remove }) => <>{fields.map(field => <Row gutter={12} key={field.key}>
            <Col xs={24} md={9}><Form.Item name={[field.name, 'source']} label="服务器目录" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={9}><Form.Item name={[field.name, 'target']} label="容器目录" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={12} md={3}><Form.Item name={[field.name, 'read_only']} label="只读" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={12} md={3}><Button style={{ marginTop: 30 }} onClick={() => remove(field.name)}>移除</Button></Col>
          </Row>)}<Button onClick={() => add({ read_only: true })}>添加挂载</Button></>}</Form.List></> },
        ]} />
        {saveError && <Alert type="error" role="alert" title={saveError} showIcon style={{ marginBottom: 12 }} />}
        <Space wrap><Button type="primary" htmlType="submit" loading={saving}>保存任务配置</Button><Button onClick={() => setOpen(false)}>取消</Button><Typography.Text type="secondary">待启动任务可修改；保存后须重新预检查，保存不会启动训练。</Typography.Text></Space>
      </Form>
    </Modal>
    <Drawer open={!!selected && !open} width={1000} title={selected?.name || '训练详情'} onClose={() => { if (!busy) setSelected(undefined); }} closable={!busy} mask={{ closable: !busy }}>
      {selected && <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap>{status(selected)}{admin && <>
          <Button disabled={!!busy || selected.status !== 'draft'||outputKinds.includes(selected.kind)} onClick={() => showEdit(selected)}>编辑配置</Button>
          {!outputKinds.includes(selected.kind)&&<Button disabled={!!busy||!catalog[selected.kind]} onClick={()=>showEdit(selected,true)}>复制配置新建任务</Button>}
          <Button disabled={!!busy} onClick={() => { void action('precheck'); }}>预检查</Button>
          <Button type="primary" disabled={!!busy || selected.status !== 'draft' || checkResult?.ok !== true} onClick={() => setConfirm('start')}>{outputKinds.includes(selected.kind)?'启动执行':'启动训练'}</Button>
          <Button danger disabled={!!busy || !['draft', ...active].includes(selected.status)} onClick={() => setConfirm('stop')}>停止任务</Button>
          <Button disabled={!!busy} onClick={() => { void action('refresh'); }}>核对运行状态</Button>
          <Button danger disabled={!!busy||active.includes(selected.status)} onClick={()=>setConfirm('delete')}>删除任务</Button>
        </>}
          {selected.source.project_id&&<Link to={`/projects/${selected.source.project_id}/deliver`}>查看来源项目</Link>}
          {outputSources.includes(selected.kind)&&<Button disabled={!!busy} onClick={()=>setDetailTab('outputs')}>{['swift_sft','wemm'].includes(selected.kind)?'合并LoRA模型':'导出模型'}</Button>}
          {admin&&selected.status==='completed'&&['model_export','model_merge','swift_sft','wemm'].includes(selected.kind)&&<Link to={`/model-deployments?run_id=${selected.id}`}>归档与下发模型 →</Link>}
          {['yolo26','rfdetr_medium','rfdetr_2xlarge','dinov3_vits16_ltdetr'].includes(selected.kind)&&<Link to="/validation">验证模型权重 →</Link>}
          {admin&&<Link to={`/gpu-terminal?server_id=${encodeURIComponent(selected.server.id)}`}>调试此服务器</Link>}
        </Space>
        {busy && <Space><Spin size="small" /><Typography.Text>{busy === 'start' ? '正在预检查并准备输入，可能需要几分钟；请勿重复启动。' : busy==='download'?'正在校验并下载产物，较大权重需要一些时间，请保持窗口打开…':busy==='delete'?'正在删除平台任务记录…':'正在读取服务器状态…'}</Typography.Text></Space>}
        {(detailError || selected.error) && <Alert type="error" showIcon title={detailError || selected.error} />}
        {selected.config.onnx_stage&&<Alert type="info" showIcon title={`导出阶段：${({onnx:'训练服务器导出ONNX',transferring:'校验并自动传输ONNX',tensorrt:'目标GPU服务器构建TensorRT'} as const)[selected.inspection.output_phase||'onnx']}`} description={`${selected.config.onnx_stage.server.name} → ${selected.server.name}；完成后在“模型产物”下载ONNX和engine。`}/>}
        <Tabs activeKey={detailTab} onChange={tab => { void readOutput(tab); }} items={[
          { key:'startup',label:'启动进度',children:<TrainingStartup run={selected}/> },
          ...(!outputKinds.includes(selected.kind)?[{key:'analysis',label:'效果分析与对比',children:<TrainingAnalysis key={selected.id} run={selected} admin={admin}/>}]:[]),
          { key: 'overview', label: '任务与配置', children: <><Descriptions column={1} bordered size="small" items={[
            { key: 'id', label: '任务ID', children: selected.id }, { key: 'kind', label: '训练器', children: outputNames[selected.kind] || catalog[selected.kind]?.name || selected.kind },
            { key: 'server', label: '服务器', children: selected.server.name }, { key: 'gpu', label: 'GPU', children: selected.config.gpu_uuids.join(', ') },
            { key: 'source', label: '数据源 / 权重来源', children: selected.source.kind==='model_output'?`来源任务 ${selected.source.run_id} · ${selected.source.source_path}`:selected.source.kind === 'snapshot' ? `冻结导出 ${selected.source.id} · SHA256 ${selected.source.sha256}` : selected.config.dataset_path },
            { key: 'labels', label: '训练标签', children: selected.source.kind === 'remote' ? '沿用服务器配置' : selected.config.selected_labels?.join('、') || '全部标签' },
            { key: 'output', label: '产物目录', children: `${selected.output_dir}/${selected.config.output_subdir}` },
          ]} /><Typography.Title level={5}>保存的最终配置</Typography.Title><JsonView label="保存的最终配置" value={selected.config}/></> },
          { key: 'checks', label: '预检查', children: checkResult?.checks ? <Space orientation="vertical" style={{ width: '100%' }}>{checkResult.checks.map((check, i) => <Alert key={i} showIcon type={check.ok ? 'success' : 'error'} title={check.name} description={<JsonView label={check.name+'详情'} value={check.detail}/>} />)}</Space> : <Typography.Paragraph>保存任务后点击预检查；启动时会再次检查，避免使用过期资源状态。</Typography.Paragraph> },
          { key: 'logs', label: outputKinds.includes(selected.kind)?'执行日志':'训练日志', children: <TrainingLogs key={selected.id} text={logs} busy={!!busy} onRefresh={()=>{void readOutput('logs');}}/> },
          ...(outputSources.includes(selected.kind)?[{key:'outputs',label:['swift_sft','wemm'].includes(selected.kind)?'LoRA合并':'模型导出',children:<ModelOutputForm key={selected.id} run={selected} servers={servers} admin={admin} initialSource={outputPath} onCreated={job=>{void inspect(job);setReload(n=>n+1);}}/>}]:[]),
          { key: 'artifacts', label: '模型产物', children: <>{truncated && <Alert type="warning" title="仅显示前500个文件，完整产物保留在服务器目录。" />}<Table rowKey="path" scroll={{ x: 650 }} dataSource={files} pagination={{ pageSize: 20 }} columns={[{ title: '文件', dataIndex: 'path' }, { title: '大小', dataIndex: 'bytes',render:value=>value<1024?`${value} B`:`${(value/1024**2).toFixed(2)} MiB` }, { title: '类型', dataIndex: 'role', render: value => value === 'weights' ? '模型权重' : '配置/日志' },{title:'操作',render:(_,file)=><Space wrap><Button disabled={!!busy} onClick={()=>{void download(file);}}>下载到本地</Button>{admin&&['yolo26','rfdetr_medium','rfdetr_2xlarge'].includes(selected.kind)&&/\.(pt|pth)$/.test(file.path)&&<Button disabled={!!busy} onClick={()=>{setOutputPath(`${selected.output_dir}/${selected.config.output_subdir}/${file.path}`);setDetailTab('outputs');}}>导出此权重</Button>}</Space>}]} /><Typography.Paragraph type="secondary">可下载模型权重、配置和日志；训练中的文件如发生变化会拒绝本次下载，请选稳定检查点或结束后重试。</Typography.Paragraph></> },
        ]} />
      </Space>}
    </Drawer>
    <Modal open={!!confirm} title={confirm === 'start' ? (selected&&outputKinds.includes(selected.kind)?'启动此导出 / 合并任务？':'启动此训练任务？') : confirm==='delete'?'删除此任务记录？':'停止此任务？'} confirmLoading={!!busy} okText={confirm === 'start' ? '确认启动' : confirm==='delete'?'确认删除':'确认停止'} cancelText="返回" okButtonProps={{ danger: confirm !== 'start' }} onCancel={() => { if (!busy) setConfirm(undefined); }} onOk={() => { if (confirm) void action(confirm); }}>
      <Typography.Paragraph>{confirm === 'start' ? (selected&&outputKinds.includes(selected.kind)?'将重新检查来源、镜像和GPU，创建独立导出/合并容器，占用所选GPU；输入只读，结果写入新任务目录。':'将重新检查镜像、数据和GPU，然后创建独立Docker容器。不会停止其他服务。冻结包需先传输并校验，训练会占用所选GPU。') : confirm==='delete'?'只移除平台任务记录。服务器上的模型权重、日志、数据和容器均保留；删除后该任务入口消失，请先下载所需产物。已有验证记录保留。':'只停止本平台记录的对应任务容器，保留数据、日志和已保存产物；确认停止后释放GPU占用。'}</Typography.Paragraph>
      {detailError && <Alert type="error" title={detailError} />}
    </Modal>
  </Space>;
}
