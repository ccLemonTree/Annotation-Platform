import { useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Checkbox, Col, Collapse, Descriptions, Drawer, Form, Input, InputNumber, Modal, Row, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography } from 'antd';
import { getGpuResources } from '../api/gpuResources';
import type { GpuResource, GpuServer } from '../api/gpuResources';
import { getProjects } from '../api/projects';
import type { Project } from '../api/projects';
import { createTrainingRun, updateTrainingRun, deleteTrainingRun, downloadTrainingArtifact, getTrainingArtifacts, getTrainingCatalog, getTrainingK8sNodes, getTrainingDefaults, getTrainingLogs, getTrainingRun, getTrainingRuns, getTrainingServers, getTrainingSnapshots, getTrainingTrainers, trainingAction } from '../api/training';
import { archiveTrainingModel, downloadRepositoryModel, getRepositoryModel, getRepositoryModels } from '../api/modelRepository';
import type { RepositoryModel } from '../api/modelRepository';
import { ParameterInput, parameterLabels } from '../components/TrainingParameterInput';
import TrainingLogs from '../components/TrainingLogs';
import TrainingStartup from '../components/TrainingStartup';
import TrainingAnalysis from '../components/TrainingAnalysis';
import TrainingPlatformData from '../components/TrainingPlatformData';
import TrainingTrainerManager, { parseTrainingParameters } from '../components/TrainingTrainerManager';
import ModelOutputForm from '../components/ModelOutputForm';
import JsonView from '../components/JsonView';
import { useCurrentUser } from '../components/currentUser';
import type { TrainingArtifact, TrainingCheck, TrainingInput, TrainingRun, TrainingSnapshot, TrainingTemplate, TrainingK8sInfo, TrainingTrainer } from '../api/training';

const names: Record<string, string> = { draft: '待启动', starting: '准备中', running: '训练中', stopping: '停止中', unknown: '状态待核对', completed: '已完成', failed: '失败', canceled: '已取消' };
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
const k8sKinds=['yolo26','rfdetr_medium','rfdetr_2xlarge','wemm'];
function snapshotSummary(snapshot:TrainingSnapshot) {
  const assets = snapshot.manifest.assets || [], counts = {train:0,val:0,test:0};
  let invalid = 0, pending = 0, rejected = 0, unknown = 0;
  for (const asset of assets) {
    if (asset.split === 'train' || asset.split === 'val' || asset.split === 'test') counts[asset.split]++; else invalid++;
    if (asset.review_status === 'pending') pending++;
    else if (asset.review_status === 'rejected') rejected++;
    else if (asset.review_status !== 'approved' && !(snapshot.manifest.approved_only === true && !Object.prototype.hasOwnProperty.call(asset,'review_status'))) unknown++;
  }
  const problems = [!counts.train && '缺少训练集', !counts.val && '缺少验证集', invalid > 0 && `存在无效划分（${invalid} 张）`, pending > 0 && snapshot.manifest.approved_only !== false && `待审核 ${pending} 张，超出导出时的审核范围`, rejected > 0 && `已驳回 ${rejected} 张`, unknown > 0 && `缺少审核状态 ${unknown} 张`].filter(Boolean);
  return {counts, problems, pending};
}
const outputKinds=['model_export','model_merge'];
const exportSources=['yolo26','rfdetr_medium','rfdetr_2xlarge'];
const mergeSources=['swift_sft','wemm'];
const isJsonlTrainer = (trainer?: TrainingTrainer) => !!trainer && !['yolo', 'coco'].includes(trainer.dataset_format);
const outputNames:Record<string,string>={model_export:'模型导出',model_merge:'LoRA合并'};
function status(run: TrainingRun) { return <Tag color={run.status === 'completed' ? 'success' : run.status === 'failed' || run.status === 'unknown' ? 'error' : active.includes(run.status) ? 'processing' : 'default'}>{run.status==='running'&&outputKinds.includes(run.kind)?'执行中':names[run.status] || run.status}</Tag>; }


export default function TrainingPage({ embeddedProject }: { embeddedProject?: Project }) {
  const [params]=useSearchParams();
  const embeddedProjectId=embeddedProject?.id;
  const contextId=embeddedProjectId || params.get('project_id');
  const [form] = Form.useForm();
  const admin = useCurrentUser()?.role === 'admin';
  const [catalog, setCatalog] = useState<Record<string, TrainingTemplate>>({}), [servers, setServers] = useState<GpuServer[]>([]), [projects, setProjects] = useState<Project[]>([]);
  const [trainers, setTrainers] = useState<TrainingTrainer[]>([]);
  const [savedTrainer, setSavedTrainer] = useState<TrainingTrainer>();
  const [manageTrainers, setManageTrainers] = useState(false);
  const [trainersError, setTrainersError] = useState('');
  const trainingServers = servers.filter(server => server.enabled && (server.id === 'default' || server.training_enabled && server.has_password));
  const [runs, setRuns] = useState<TrainingRun[]>([]), [total, setTotal] = useState(0), [page, setPage] = useState(1), [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [open, setOpen] = useState(false), [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [advancedKeys,setAdvancedKeys]=useState<string[]>([]),[configKeys,setConfigKeys]=useState<string[]>([]);
  const [repositoryChoices,setRepositoryChoices]=useState<RepositoryModel[]>([]);
  const repositoryRequest=useRef<AbortController|null>(null);
  const [repositoryLoading,setRepositoryLoading]=useState(false),[repositoryError,setRepositoryError]=useState('');
  const [kind, setKind] = useState('yolo26'), [serverId, setServerId] = useState(''), [gpus, setGpus] = useState<GpuResource[]>([]), [gpuLoading, setGpuLoading] = useState(false), [gpuError, setGpuError] = useState('');
  const duplicateDefault=trainingServers.some(server=>server.duplicate_of_default);
  const serverChoices=trainingServers.filter(server=>server.id===serverId||
    (server.id==='default'?!duplicateDefault:!server.duplicate_of_default||serverId!=='default'));
  const [backend, setBackend] = useState<'docker'|'k8s'>('docker'), [k8sInfo, setK8sInfo] = useState<TrainingK8sInfo>(), [k8sError, setK8sError] = useState(''), [logNode, setLogNode] = useState<string>();
  const nodes = Form.useWatch(['k8s','nodes'], form) as string[] | undefined, gpusPerNode = Form.useWatch(['k8s','gpus_per_node'], form) as number | undefined;
  const [snapshots, setSnapshots] = useState<TrainingSnapshot[]>([]), [snapshotLoading, setSnapshotLoading] = useState(false);
  const snapshotRequest=useRef<AbortController|null>(null);
  useEffect(()=>{
    if (!open) return;
    return ()=>{snapshotRequest.current?.abort();repositoryRequest.current?.abort();};
  },[open]);
  const [selected, setSelected] = useState<TrainingRun>(), [detailTab, setDetailTab] = useState('overview'), [busy, setBusy] = useState(''), [detailError, setDetailError] = useState('');
  const selectedId=selected?.id,selectedStatus=selected?.status;
  const canExport=!!selected&&exportSources.includes(selected.kind);
  const canMerge=!!selected&&selected.status==='completed'&&mergeSources.includes(selected.kind)&&selected.config.parameters.tuner_type==='lora';
  const canReuseFullModel=!!selected&&selected.status==='completed'&&mergeSources.includes(selected.kind)&&selected.config.parameters.tuner_type==='full';
  const outputRequest=useRef(0),outputController=useRef<AbortController|null>(null);
  const outputBusy=['logs','artifacts'].includes(busy);
  useEffect(()=>()=>{outputRequest.current++;outputController.current?.abort();},[]);
  const [logs, setLogs] = useState(''), [files, setFiles] = useState<TrainingArtifact[]>([]), [truncated, setTruncated] = useState(false);
  const [confirm, setConfirm] = useState<'start' | 'stop' | 'delete'>();
  const [datasetLabels, setDatasetLabels] = useState<string[]>([]);
  const [editing, setEditing] = useState<string>(), [restoring, setRestoring] = useState(false);
  const [outputPath,setOutputPath]=useState<string>();
  const [outputEdit,setOutputEdit]=useState<{job:TrainingRun;parent:TrainingRun}>();
  const trainer = savedTrainer?.id === kind ? savedTrainer : trainers.find(row => row.id === kind);
  const swift = kind === 'swift_sft' || kind === 'wemm' || isJsonlTrainer(trainer);
  const loadRepositoryChoices=async(q='')=>{
    repositoryRequest.current?.abort();
    const controller=new AbortController();
    repositoryRequest.current=controller;
    setRepositoryLoading(true);setRepositoryError('');
    try {
      const result=await getRepositoryModels({page:1,format:'huggingface',q},controller.signal);
      if (!controller.signal.aborted) setRepositoryChoices(previous=>[
        ...result.items.filter(row=>row.status==='ready'&&row.entry_file.endsWith('config.json')&&!row.entry_file.endsWith('adapter_config.json')),
        ...previous.filter(row=>row.id===form.getFieldValue('repository_model_id')&&!result.items.some(item=>item.id===row.id)),
      ]);
    } catch (cause) {
      if (!controller.signal.aborted) setRepositoryError(errorText(cause));
    } finally {
      if (!controller.signal.aborted) setRepositoryLoading(false);
    }
  };
  const dinov3 = kind === 'dinov3_vits16_ltdetr';
  const allowedFormats = catalog[kind]?.dataset_formats;
  const modelSize = catalog[kind]?.schema.properties.size;
  const taskType=Form.useWatch(['parameters','task_type'],form),lossType=Form.useWatch(['parameters','loss_type'],form);
  const selectedSnapshotId=Form.useWatch('snapshot_id',form) as string | undefined;
  const selectedProjectId=Form.useWatch('project_id',form) as string | undefined;
  const compatibleSnapshots=useMemo(()=>snapshots.filter(s => trainer ? s.format===trainer.dataset_format :
    allowedFormats ? allowedFormats.some(format => format === s.format) : s.format==='yolo'||kind!=='yolo26'&&s.format==='coco'
  ).map(snapshot=>({snapshot,...snapshotSummary(snapshot)})),[snapshots,kind,trainer,allowedFormats]);
  const selectedSnapshot=compatibleSnapshots.find(row=>row.snapshot.id===selectedSnapshotId);
  const snapshotInfo=selectedSnapshot;
  const datasetFormat=trainer?.dataset_format || (taskType==='embedding'?(lossType||'infonce'):'swift_sft');
  const context=embeddedProject || projects.find(project=>project.id===contextId);
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    setTrainersError('');
    getTrainingTrainers(controller.signal).then(rows => {
      if (!controller.signal.aborted) setTrainers(rows);
    }).catch(cause => {
      if (!controller.signal.aborted) setTrainersError(errorText(cause));
    });
    return () => controller.abort();
  }, [admin]);
  useEffect(() => {
    const controller = new AbortController();
    getTrainingCatalog(controller.signal).then(setCatalog).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
    if (admin) {
      getTrainingServers(controller.signal).then(setServers).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
      if(!embeddedProjectId)(async()=>{const rows:Project[]=[];
        for(let page=1;!controller.signal.aborted;page++){
          const result=await getProjects({page,page_size:100});rows.push(...result.items);
          if(rows.length>=result.total||!result.items.length)break;
        }
        if(!controller.signal.aborted)setProjects(rows);
      })().catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
    }
    return () => controller.abort();
  }, [admin,embeddedProjectId]);
  useEffect(() => {
    const controller = new AbortController(); let pending = false;
    const load = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const result = await getTrainingRuns(page, controller.signal, contextId || undefined);
        if (!controller.signal.aborted) { setRuns(result.items); setTotal(result.total); setError(''); }
      } catch (cause) { if (!controller.signal.aborted) setError(errorText(cause)); }
      finally { pending = false; if (!controller.signal.aborted) setLoading(false); }
    };
    void load(); const timer = setInterval(() => { void load(); }, 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [page, reload, contextId]);
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
    if (!open || !admin || backend !== 'docker' || !serverId) return;
    const controller = new AbortController();
    getGpuResources(controller.signal, serverId).then(result => { if (!controller.signal.aborted) setGpus(result.gpus); })
      .catch(e => { if (!controller.signal.aborted) setGpuError(errorText(e)); })
      .finally(() => { if (!controller.signal.aborted) setGpuLoading(false); });
    return () => controller.abort();
  }, [open, serverId, admin, backend]);
  useEffect(() => {
    if (!open || !admin || backend !== 'docker' || !serverId) return;
    const controller = new AbortController();
    getTrainingDefaults(serverId, kind, controller.signal).then(values => {
      if (controller.signal.aborted) return;
      if (!allowedFormats && !form.getFieldValue('model_path')) form.setFieldValue('model_path', values.model_path);
      if (!form.getFieldValue('output_root')) form.setFieldValue('output_root', values.output_root);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)); });
    return () => controller.abort();
  }, [open, serverId, kind, admin, form, backend, allowedFormats]);
  useEffect(() => {
    if (!open || !admin || backend !== 'k8s') return;
    const controller = new AbortController(); setK8sError('');
    getTrainingK8sNodes(controller.signal).then(info => { setK8sInfo(info); if (!restoring) { const currentKind = form.getFieldValue('kind'); if (currentKind === 'wemm') { const defaults = info.wemm_defaults; if (!form.getFieldValue('image')) { form.setFieldValue('image', info.images.wemm === '(later)' ? '' : info.images.wemm); form.setFieldsValue({parameters:{...catalog.wemm?.defaults,...defaults?.parameters},environment:{...catalog.wemm?.environment,...defaults?.environment}}); } if (!form.getFieldValue('model_path')) form.setFieldValue('model_path', defaults?.model_path); } else if (!form.getFieldValue('image') || form.getFieldValue('image') === 'aisfh5.com:9091/ai/jashan_train:yolo-rfdetr') form.setFieldValue('image', info.images.detection); } }).catch(e => { if (!controller.signal.aborted) setK8sError(errorText(e)); });
    return () => controller.abort();
  }, [open, admin, backend, restoring, form, catalog]);
  const clearLabels = () => {
    setDatasetLabels([]); form.setFieldValue('selected_labels', null);
  };
  const loadSnapshots = (projectId:string) => {
    snapshotRequest.current?.abort();
    const controller=new AbortController();
    snapshotRequest.current=controller;
    setError('');setSnapshots([]);setSnapshotLoading(true);
    getTrainingSnapshots(projectId,false,controller.signal).then(rows=>{if(!controller.signal.aborted)setSnapshots(rows);})
      .catch(cause=>{if(!controller.signal.aborted)setError(errorText(cause));}).finally(()=>{if(!controller.signal.aborted)setSnapshotLoading(false);});
  };
  const k8sImage = (value:string) => value === 'wemm' ? k8sInfo?.images.wemm || '' : k8sInfo?.images.detection || 'aisfh5.com:9091/ai/jashan_train:yolo-rfdetr';
  const applyBuiltinDefaults = (value: string, template: TrainingTemplate) => {
    setCatalog(current => ({ ...current, [value]: template }));
    if (value === 'wemm') setK8sInfo(undefined);
  };
  const changeKind = (value: string) => {
    snapshotRequest.current?.abort();setSnapshotLoading(false);
    setRestoring(false); setKind(value);setSnapshots([]);
    clearLabels();
    const registered = savedTrainer?.id === value ? savedTrainer : trainers.find(row => row.id === value);
    form.setFieldsValue({ kind: value, model_path: backend === 'k8s' && value === 'wemm' ? k8sInfo?.wemm_defaults?.model_path : undefined, image: registered?.image ?? (backend === 'k8s' ? k8sImage(value) : catalog[value].image), parameters: registered ? {} : backend === 'k8s' ? {...catalog[value].defaults,...(value === 'wemm' ? k8sInfo?.wemm_defaults?.parameters : value === 'yolo26' ? {device:'auto'} : {devices:'auto'})} : catalog[value].defaults, environment: registered ? {} : backend === 'k8s' && value === 'wemm' ? {...catalog[value].environment,...k8sInfo?.wemm_defaults?.environment} : catalog[value].environment, accept_platform_model_license: false, snapshot_id: undefined,project_id:context?.id || form.getFieldValue('project_id') });
    form.setFieldValue('trainer_parameters', registered ? JSON.stringify(registered.parameters, null, 2) : undefined);
    if (registered) form.setFieldValue('mounts', []);
    const projectId=form.getFieldValue('project_id');
    if(projectId&&!isJsonlTrainer(registered)&&!['swift_sft','wemm'].includes(value))loadSnapshots(projectId);
  };
  const showCreate = () => {
    setSavedTrainer(undefined);
    setAdvancedKeys([]);setConfigKeys([]);
    const initialKind=context?.task_type==='vector'?'wemm':context&&['caption','classification'].includes(context.task_type)?'swift_sft':'yolo26';
    setBackend('docker'); setRestoring(false); setEditing(undefined);
    setSaveError('');
    const initialServer = trainingServers.find(server=>server.duplicate_of_default)?.id || trainingServers[0]?.id;
    setError(''); setKind(initialKind); setServerId(initialServer || ''); setSnapshots([]);
    setGpuLoading(!!initialServer); setGpuError(''); setGpus([]);
    clearLabels();
    form.resetFields(); form.setFieldsValue({ execution_backend:'docker', kind: initialKind, server_id: initialServer, image: catalog[initialKind]?.image,
      project_id:context?.id, output_subdir: 'train', shm_size_gb: 16, parameters: catalog[initialKind]?.defaults, environment: catalog[initialKind]?.environment, mounts: [], accept_platform_model_license: false });
    if(context&&!['swift_sft','wemm'].includes(initialKind))loadSnapshots(context.id);
    setOpen(true);
  };
  const showEdit = (run: TrainingRun, copy = false, modelPath?: string, repositoryId?: string) => {
    setAdvancedKeys(modelPath?['advanced']:[]);setConfigKeys([]);
    setSaveError(''); setRestoring(true); setBackend(run.config.execution_backend || 'docker');
    const usableServer = run.config.execution_backend === 'k8s' || trainingServers.some(server => server.id === run.server.id);
    const registration = run.config.trainer_id && run.config.trainer ? { id: run.config.trainer_id, ...run.config.trainer } : undefined;
    const currentTrainer = copy ? trainers.find(row => row.id === registration?.id) : registration;
    const choice = currentTrainer?.id || run.kind;
    setSavedTrainer(copy ? undefined : registration);
    setEditing(copy ? undefined : run.id); setError(''); setKind(choice); setServerId(usableServer ? run.server.id : '');
    setGpuLoading(usableServer); setGpuError(''); setGpus([]); setSnapshots([]);
    setDatasetLabels(run.source.labels || []);
    form.resetFields();
    const values = structuredClone(run.config) as TrainingInput & { adapter_version?: number; runtime_dataset?: string };
    delete values.adapter_version; delete values.runtime_dataset;
    if (run.kind === 'yolo11' && values.parameters.optimizer === undefined) values.parameters.optimizer = catalog.yolo11?.defaults.optimizer;
    const sameFormat = !copy || currentTrainer?.dataset_format === registration?.dataset_format;
    const trainerParameters = copy && currentTrainer
      ? Object.fromEntries(Object.entries(currentTrainer.parameters).map(([key, value]) => [
        key, typeof values.parameters[key] === typeof value && Array.isArray(values.parameters[key]) === Array.isArray(value)
          ? values.parameters[key] : value,
      ]))
      : values.parameters;
    form.setFieldsValue({ ...values, kind: choice, execution_backend:values.execution_backend || 'docker', name: copy ? `${run.name.slice(0,197)} 副本` : run.name, server_id: usableServer ? run.server.id : undefined, ...(!usableServer ? {gpu_uuids:[],model_path:undefined} : {}), project_id: context?.id || run.source.project_id, dataset_path: '', snapshot_id: sameFormat && run.source.kind === 'snapshot' ? run.config.snapshot_id : undefined,
      ...(currentTrainer ? { image: currentTrainer.image, trainer_parameters: JSON.stringify(trainerParameters, null, 2) } : {}) });
    if(modelPath)form.setFieldValue('model_path',modelPath);
    form.setFieldValue('repository_model_id',repositoryId||(!modelPath?run.source.repository_model_id:undefined));
    const projectId=context?.id || run.source.project_id;
    if(projectId&&!isJsonlTrainer(currentTrainer)&&!['swift_sft','wemm'].includes(run.kind))loadSnapshots(projectId);
    setOpen(true);
  };
  const trainFromMerged = async(run:TrainingRun) => {
    setBusy('edit');setDetailError('');
    try {
      const parent=await getTrainingRun(run.source.run_id!);
      const result=await getRepositoryModels({page:1,format:'huggingface',run_id:run.id});
      const archived=result.items.find(row=>row.run_id===run.id&&row.entry_file==='merged/config.json'&&row.status==='ready');
      if(!archived){setDetailError('请等合并模型入库完成后再创建训练任务');return;}
      setRepositoryChoices(rows=>[archived,...rows.filter(row=>row.id!==archived.id)]);
      showEdit(parent,true,run.output_dir+'/'+run.config.output_subdir+'/merged',archived.id);
    } catch(cause) {setDetailError(errorText(cause));}
    finally {setBusy('');}
  };
  const showOutputEdit = async(job:TrainingRun) => {
    setBusy('edit');setDetailError('');
    try{setOutputEdit({job,parent:await getTrainingRun(job.source.run_id!)});}
    catch(cause){setDetailError(errorText(cause));}
    finally{setBusy('');}
  };
  const save = async (values: TrainingInput) => {
    if (!swift) {
      const snapshot = snapshots.find(row => row.id === values.snapshot_id);
      if (!snapshot) { setSaveError('冻结版本尚未加载，请稍候重试'); return; }
      const {problems} = snapshotSummary(snapshot);
      if (problems.length) { setSaveError('冻结版本不满足训练要求：' + problems.join('；')); return; }
    }
    setSaving(true); setSaveError('');
    values = {...values, parameters:form.getFieldValue('parameters'), environment:form.getFieldValue('environment')};
    try {
      const defaults = backend === 'k8s' && kind === 'wemm' ? {...catalog[kind].defaults,...k8sInfo?.wemm_defaults?.parameters} : catalog[kind]?.defaults || {};
      const parameters = trainer ? parseTrainingParameters(form.getFieldValue('trainer_parameters')) : Object.fromEntries(Object.keys(defaults).map(key => {
        const value = values.parameters?.[key];
        const nullable = catalog[kind]?.schema.properties[key]?.anyOf?.some(field => field.type === 'null');
        return [key, value ?? (nullable && values.parameters && key in values.parameters ? null : defaults[key])];
      }));
      const input: TrainingInput = { ...values, parameters, environment: Object.fromEntries(Object.entries(values.environment || {}).filter(([key]) => key in (catalog[kind]?.environment || {}) || (backend === 'k8s' && kind === 'wemm' && key in (k8sInfo?.wemm_defaults?.environment || {})))),
        mounts: values.mounts || [], dataset_path: '', snapshot_id: values.snapshot_id,
        selected_labels: !swift ? values.selected_labels ?? null : null };
      if(input.repository_model_id)input.model_path='';
      if (backend === 'k8s') {
        input.execution_backend = 'k8s'; input.gpu_uuids = []; input.mounts = [];
        delete (input as Partial<TrainingInput>).server_id;
        if (kind === 'yolo26') input.parameters.device = 'auto';
        if (kind.startsWith('rfdetr_')) input.parameters.devices = 'auto';
      } else { input.execution_backend = 'docker'; delete input.k8s; }
      if (trainer) {
        input.kind = 'custom'; input.trainer_id = trainer.id; input.image = trainer.image; input.mounts = [];
      }
      if (!trainer) delete input.trainer_id;
      delete input.trainer;
      delete (input as TrainingInput & { trainer_parameters?: string }).trainer_parameters;
      // Project selection is only a UI filter; the backend scopes the immutable snapshot itself.
      delete (input as TrainingInput & { project_id?: string }).project_id;
      const run = editing ? await updateTrainingRun(editing, input) : await createTrainingRun(input); setOpen(false); inspect(run); setReload(n => n + 1);
    }
    catch (cause) { setSaveError(errorText(cause)); }
    finally { setSaving(false); }
  };
  const inspect = (run: TrainingRun) => { outputRequest.current++;outputController.current?.abort();outputController.current=null;if(outputBusy)setBusy('');setTruncated(false);setSelected(run); setDetailTab('overview'); setDetailError(''); setLogs(''); setLogNode(undefined); setFiles([]);setOutputPath(undefined); };
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
  const readOutput = async (tab: string, node = logNode) => {
    setDetailTab(tab);
    if(!['logs','artifacts'].includes(tab)){outputRequest.current++;outputController.current?.abort();outputController.current=null;if(outputBusy)setBusy('');return;}
    if(!selected || busy&&!outputBusy)return;
    outputController.current?.abort();
    const controller=new AbortController();outputController.current=controller;
    const request=++outputRequest.current;
    setBusy(tab); setDetailError('');
    try {
      if (tab === 'logs') {const result=await getTrainingLogs(selected.id,node,{signal:controller.signal,skipErrorToast:true});if(request===outputRequest.current)setLogs(result.text);}
      else {const result=await getTrainingArtifacts(selected.id,{signal:controller.signal,skipErrorToast:true});if(request===outputRequest.current){setFiles(result.files);setTruncated(result.truncated);}}
    } catch (cause) {if(request===outputRequest.current)setDetailError(errorText(cause));}
    finally {if(request===outputRequest.current){outputController.current=null;setBusy('');}}
  };
  const download = async (file:TrainingArtifact) => {
    if(!selected||busy)return;
    setBusy('download');setDetailError('');
    try{await downloadTrainingArtifact(selected.id,file.path);}catch(cause){setDetailError(errorText(cause));}finally{setBusy('');}
  };
  const archiveAndDownload = async(path:string) => {
    if(!selected||busy)return;
    setBusy('download');setDetailError('');
    try {
      let model=await archiveTrainingModel(selected.id,path);
      while(model.status==='importing'){
        await new Promise(resolve=>window.setTimeout(resolve,2500));
        model=await getRepositoryModel(model.id);
      }
      if(model.status!=='ready')throw new Error(model.error||'模型入库失败');
      await downloadRepositoryModel(model);
    } catch(cause){setDetailError(errorText(cause));}
    finally{setBusy('');}
  };
  const checkResult = selected?.inspection as TrainingCheck | undefined;
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {context&&!embeddedProject&&<Alert type="info" showIcon title={`使用项目数据：${context.project_name}`} description="本页仅显示本项目任务；创建时已锁定项目，并预选适用训练器。" action={<Link to={`/projects/${context.id}/deliver`}>返回项目导出与训练</Link>}/>}
    {!admin && <Alert type="info" showIcon title="训练任务只读" />}
    {error && !open && <Alert type="error" showIcon title={error} />}
    {trainersError && <Alert type="warning" showIcon title={`登记训练器读取失败：${trainersError}`} />}
    <Card title="训练任务" extra={<Space wrap><Button onClick={() => setReload(n => n + 1)}>刷新列表</Button>{admin && <><Button onClick={() => setManageTrainers(true)}>管理训练器</Button><Button type="primary" disabled={!catalog.yolo26} onClick={showCreate}>创建训练任务</Button></>}</Space>}>
      <Table rowKey="id" dataSource={runs} loading={loading} scroll={{ x: 900 }} pagination={{ current: page, pageSize: 20, total, onChange: setPage, showSizeChanger: false }} columns={[
        { title: '任务', dataIndex: 'name', render: (_, run) => <Button type="link" size="small" onClick={() => { void inspect(run); }}>{run.name}</Button> },
        { title: '训练器', render: (_, run) => run.config.trainer?.name || outputNames[run.kind] || catalog[run.kind]?.name || run.kind }, { title: '运行环境', render: (_, run) => run.config.execution_backend === 'k8s' ? '嘉善移动K8s' : 'Docker' }, { title: '服务器 / 节点', render: (_, run) => run.config.execution_backend === 'k8s' ? run.config.k8s?.nodes.join('、') : run.server.name },
        { title: 'GPU数', render: (_, run) => run.kind==='model_export'&&run.config.output?.format==='onnx'?0:run.config.execution_backend === 'k8s' ? `${run.config.k8s?.nodes.length || 0} × ${run.config.k8s?.gpus_per_node || 0} = ${(run.config.k8s?.nodes.length || 0) * (run.config.k8s?.gpus_per_node || 0)}` : run.config.gpu_uuids.length }, { title: '状态', render: (_, run) => status(run) },
        { title: '创建时间', render: (_, run) => new Date(run.created_at).toLocaleString('zh-CN') },
        { title: '操作', render: (_, run) => <Space size="small" wrap><Button size="small" onClick={() => { void inspect(run); }}>查看详情</Button>{run.status!=='draft'&&!outputKinds.includes(run.kind)&&<Button size="small" onClick={()=>{void inspect(run);setDetailTab('analysis');}}>效果分析与对比</Button>}</Space> },
      ]} />
    </Card>
    {manageTrainers && <TrainingTrainerManager catalog={catalog} onBuiltinChange={applyBuiltinDefaults}
      trainers={trainers} onChange={setTrainers} onClose={() => setManageTrainers(false)} errorText={errorText} />}
    <Modal open={open} title={editing ? '编辑训练任务配置' : '创建训练任务'} width={1000} styles={{ body: { maxHeight: '75dvh', overflowY: 'auto' } }} footer={null} mask={{ closable: false }} destroyOnHidden onCancel={() => { if (!saving) setOpen(false); }} closable={!saving}>
      {error && <Alert type="error" title={error} showIcon style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical" onFinish={values => { void save(values); }} onFinishFailed={({errorFields})=>{
        setSaveError('配置未保存：'+errorFields.flatMap(field=>field.errors).join('；'));
        const first=errorFields[0];if(!first)return;
        const field=String(first.name[0]);
        if(['model_path','image','output_root','output_subdir','shm_size_gb','parameters','trainer_parameters','environment','mounts'].includes(field))setAdvancedKeys(['advanced']);
        if(field==='environment'||field==='mounts')setConfigKeys(keys=>Array.from(new Set([...keys,field==='environment'?'env':'mounts'])));
        requestAnimationFrame(()=>form.scrollToField(first.name,{block:'center',focus:true}));
      }} disabled={saving} onValuesChange={changed=>{if(changed.parameters&&('task_type' in changed.parameters||'loss_type' in changed.parameters))form.setFieldValue('snapshot_id',undefined);}}>
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请填写任务名称' }]}><Input maxLength={200} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="execution_backend" label="运行环境" rules={[{required:true}]}><Select options={[{value:'docker',label:'Docker'},{value:'k8s',label:'嘉善移动K8s'}]} onChange={(value:'docker'|'k8s') => {
            setRestoring(false); setBackend(value);
            const nextKind = value === 'k8s' && !k8sKinds.includes(kind) ? 'yolo26' : kind;
            if (nextKind !== kind) { clearLabels(); form.setFieldsValue({snapshot_id:undefined, project_id:context?.id || form.getFieldValue('project_id')}); const id=form.getFieldValue('project_id');if(id)loadSnapshots(id); }
            setKind(nextKind);
            form.setFieldsValue({kind:nextKind, model_path:value === 'k8s' && nextKind === 'wemm' ? k8sInfo?.wemm_defaults?.model_path : undefined, output_root:value === 'k8s' ? k8sInfo?.output_root || '/adb-ssd-mount/ai/chen/training/runs' : undefined, image:value === 'k8s' ? k8sImage(nextKind) : catalog[nextKind]?.image, parameters:{...catalog[nextKind]?.defaults,...(value === 'k8s' ? nextKind === 'yolo26' ? {device:'auto'} : nextKind.startsWith('rfdetr_') ? {devices:'auto'} : k8sInfo?.wemm_defaults?.parameters : {})}, environment:value === 'k8s' && nextKind === 'wemm' ? {...catalog[nextKind]?.environment,...k8sInfo?.wemm_defaults?.environment} : catalog[nextKind]?.environment, mounts:[], k8s:{nodes:[],gpus_per_node:1}});
          }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="kind" label="训练器" rules={[{ required: true }]}><Select aria-label="训练器" onChange={changeKind} options={[
            ...Object.entries(catalog).filter(([value]) => backend === 'docker' || k8sKinds.includes(value)).map(([value, template]) => ({ value, label: template.name })),
            ...(backend === 'docker' ? [...trainers.filter(row => row.id !== savedTrainer?.id), ...(savedTrainer ? [savedTrainer] : [])].map(row => ({ value: row.id, label: `${row.name} · ${row.dataset_format}` })) : []),
          ]} /></Form.Item></Col>
          {modelSize && <Col xs={24} md={12}><Form.Item name={['parameters','size']} label="模型尺寸" rules={[{ required: true }]}>
            <ParameterInput name="size" field={modelSize} />
          </Form.Item></Col>}
          {backend === 'docker' ? <><Col xs={24} md={12}><Form.Item name="server_id" label="训练服务器" rules={[{ required: true }]} extra={!trainingServers.length?<Link to="/gpu-servers">前往服务器管理指定训练服务器</Link>:undefined}><Select onChange={id => { setServerId(id); setGpuLoading(true); setGpuError(''); setGpus([]); form.setFieldsValue({ gpu_uuids: [], model_path: undefined, output_root: undefined }); }} placeholder="选择已指定的训练服务器" options={serverChoices.map(s => ({ value: s.id, label: s.name }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="gpu_uuids" label="物理GPU（明确分配）" rules={[{ required: true, message: '请选择至少一张物理GPU' }]}><Select mode="multiple" loading={gpuLoading} placeholder="选择GPU；启动前再次检查占用" options={gpus.map(g => ({ value: g.uuid, label: `${g.index} · ${g.name} · 空闲${Math.round((g.memory_free_mib || 0) / 1024)}GiB` }))} /></Form.Item></Col>
          </> : <><Col xs={24} md={12}><Form.Item name={['k8s','nodes']} label="训练节点" rules={[{required:true,type:'array',min:1,message:'请选择至少一个节点'}]}><Select mode="multiple" options={(k8sInfo?.nodes || []).map(n=>({value:n.name,label:`${n.name} · ${n.ready ? 'Ready' : '未就绪'} · 可用 ${n.gpu_available}/${n.gpu_capacity} 卡`,disabled:!n.ready}))} /></Form.Item></Col><Col xs={24} md={12}><Form.Item name={['k8s','gpus_per_node']} label="每节点GPU数" rules={[{required:true,type:'number',min:1}]}><InputNumber min={1} precision={0} style={{width:'100%'}} /></Form.Item></Col><Col span={24}><Typography.Paragraph>总卡数：{(nodes?.length || 0) * (gpusPerNode || 0)}（{nodes?.length || 0} 节点 × {gpusPerNode || 0} 卡）；输出默认位于 {k8sInfo?.output_root || '/adb-ssd-mount/ai/chen/training/runs'}。冻结数据自动传输到训练PV；基础模型须预先放在同一PV中。</Typography.Paragraph></Col></>}
        </Row>
        {backend === 'k8s' && k8sError && <Alert type="error" title={k8sError} showIcon />}
        {backend === 'docker' && gpuError && <Alert type="error" title={gpuError} showIcon />}
        <Typography.Title level={5}>使用平台冻结数据训练</Typography.Title>
        {dinov3 && <Alert type="info" showIcon title="DINOv3 电瓶车等目标的监督检测微调" description="使用 dinov3/vits16-ltdetr-coco 完整预训练检测权重；先完成框标注、审核和train/val划分。原始图片不能直接用于本训练器。默认1000步、总batch 4是小数据起点，需用独立验证图片评估；不使用test集训练或选模型。" style={{ marginBottom: 16 }} />}
        {swift?<TrainingPlatformData key={datasetFormat} form={form} projects={context ? [context] : projects} format={datasetFormat} showSwiftBatchNote={!trainer} projectLocked={!!context} onOpenFreeze={()=>setOpen(false)}/>: <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="project_id" label="冻结导出所属项目" rules={[{ required: true }]}><Select disabled={!!context} showSearch optionFilterProp="label" options={(context ? [context] : projects).map(p => ({ value: p.id, label: p.project_name }))} onChange={id => { clearLabels(); form.setFieldValue('snapshot_id', undefined); loadSnapshots(id); }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name="snapshot_id" label="冻结版本" rules={[{ required: true }]} extra={<Space wrap><span>{kind === 'damo_yolo' ? '支持 YOLO 和 COCO 冻结版本。YOLO 在训练启动时转换。' : '选择同时包含训练集和验证集的冻结版本。'}</span><Button type="link" size="small" disabled={!form.getFieldValue('project_id')} loading={snapshotLoading} onClick={()=>loadSnapshots(form.getFieldValue('project_id'))}>重新读取冻结版本</Button></Space>}><Select loading={snapshotLoading} onChange={id => { clearLabels(); const available = snapshots.find(s => s.id === id)?.manifest.labels || []; setDatasetLabels(available); form.setFieldValue('selected_labels', available.length ? available : null); }} options={compatibleSnapshots.map(({snapshot:s,problems}) => ({ value: s.id, disabled: !!problems.length, label: `${s.version_label ? s.version_label+' · ' : ''}${s.format.toUpperCase()} · ${new Date(s.created_at).toLocaleString('zh-CN')} · ${s.id.slice(0, 8)}${problems.length ? ' · ' + problems.join('、') : ''}` }))} /></Form.Item></Col>
        </Row>}
        {!swift && <>
          {selectedProjectId && !snapshotLoading && !compatibleSnapshots.some(row=>!row.problems.length) && <Alert type="warning" showIcon style={{marginBottom:16}}
            title="没有可用于当前训练器的冻结版本" description={<Space wrap><span>请重新冻结同时包含训练集和验证集的版本。取消勾选「仅审核通过」可纳入待审核标注。</span><Link to={`/annotations?project_id=${selectedProjectId}&review_status=pending`}>查看待审核</Link>{!context&&<Link to={`/projects/${selectedProjectId}/deliver`}>去项目导出</Link>}</Space>}/>}
          {snapshotInfo && <Alert type={snapshotInfo.problems.length ? 'warning' : 'success'} showIcon title={`训练集 ${snapshotInfo.counts.train} · 验证集 ${snapshotInfo.counts.val} · 测试集 ${snapshotInfo.counts.test}；${snapshotInfo.problems.length ? snapshotInfo.problems.join('；') : snapshotInfo.pending ? `待审核 ${snapshotInfo.pending} 张，可用于训练` : '全部已审核通过'}`} style={{ marginBottom: 16 }} />}
          <Typography.Text type="secondary">平台自动生成训练配置</Typography.Text>
          <Form.Item name="selected_labels" label="训练标签" rules={datasetLabels.length ? [{ required: true, type: 'array', min: 1, message: '至少选择一个训练标签' }] : []} extra="默认训练全部标签；按原顺序重新编号。未选类别的框被移除，图片保留为背景样本。">
            <Select mode="multiple" disabled={!datasetLabels.length} placeholder="选择冻结版本后可选择训练标签" options={datasetLabels.map(label => ({ value: label, label }))} />
          </Form.Item>
        </>}
        {kind === 'rfdetr_2xlarge' && <Form.Item name="accept_platform_model_license" valuePropName="checked"><Checkbox>我已阅读并确认适用的 <a href="https://rfdetr.roboflow.com/latest/reference/2xlarge/" target="_blank" rel="noreferrer">Platform Model License</a></Checkbox></Form.Item>}
        <Collapse activeKey={advancedKeys} onChange={setAdvancedKeys} style={{marginBottom:20}} items={[{key:'advanced',label:'高级配置（权重路径、镜像与训练参数）',forceRender:true,children:<>
        {mergeSources.includes(kind)&&<Form.Item name="repository_model_id" label="仓库基础模型（可选）" extra="启动时自动传输，已有相同文件时复用。">
          <Select allowClear showSearch filterOption={false} loading={repositoryLoading}
            notFoundContent={repositoryLoading?'正在读取仓库模型…':repositoryError||'暂无可用的完整模型'}
            onOpenChange={visible=>{if(visible)void loadRepositoryChoices();}}
            onSearch={value=>{void loadRepositoryChoices(value);}}
            options={repositoryChoices.map(row=>({value:row.id,label:`${row.name} · ${row.version.slice(0,8)}`}))}/>
        </Form.Item>}
        <Form.Item noStyle shouldUpdate>{()=>form.getFieldValue('repository_model_id')?null:
          <Form.Item name="model_path" label={dinov3?'服务器DINOv3完整检测权重路径':allowedFormats?'服务器基础权重文件（可选）':'服务器基础权重文件或模型目录'} rules={[{required:!trainer&&!allowedFormats}]}><Input placeholder={allowedFormats?'留空时使用镜像内对应尺寸的预训练权重':'填写所选服务器上的绝对路径'}/></Form.Item>}</Form.Item>
        <Form.Item name="image" label="训练镜像" rules={[{ required: true }]} extra={trainer ? '使用登记的训练镜像。' : '需要已在服务器就绪；平台不会重复拉取镜像。'}><Input readOnly={!!trainer} /></Form.Item>
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="output_root" label="服务器输出根目录" rules={[{ required: true }]} extra="默认按北京时间生成，可修改；不存在时自动创建，每任务再增加独立UUID目录。"><Input placeholder="/ai/chen/training/runs/年月日-时分秒" /></Form.Item></Col>
          <Col xs={12} md={6}><Form.Item name="output_subdir" label="训练产物子目录" rules={[{ required: true }]}><Input /></Form.Item></Col>
          <Col xs={12} md={6}><Form.Item name="shm_size_gb" label="共享内存（GiB）" rules={[{ required: true }]} extra="容器/dev/shm上限，供数据加载进程共享数据；占用主机内存，不是显存，也不预先占满。"><InputNumber min={1} max={1024} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        {trainer ? <Form.Item name="trainer_parameters" label="训练参数（JSON）"
          rules={[{ validator: async (_, value: string) => { parseTrainingParameters(value); } }]}>
          <Input.TextArea rows={4} spellCheck={false} />
        </Form.Item> : <>
        <Typography.Title level={5}>训练参数</Typography.Title>
        <Row gutter={16}>{Object.entries(catalog[kind]?.schema.properties || {}).filter(([key]) => key !== 'size').map(([key, field]) => {
          const schema = field.anyOf?.find(v => v.type !== 'null') || field;
          return <Col xs={24} sm={12} md={8} key={kind + key}><Form.Item name={['parameters', key]} label={dinov3 && key === 'batch_size' ? '总批次大小（全部GPU）' : parameterLabels[key] || key} extra={key} valuePropName={schema.type === 'boolean' ? 'checked' : 'value'}>{backend === 'k8s' && ((kind === 'yolo26' && key === 'device') || (kind.startsWith('rfdetr_') && key === 'devices')) ? <Input value="auto" disabled /> : <ParameterInput name={key} field={field} />}</Form.Item></Col>;
        })}</Row>
        <Collapse activeKey={configKeys} onChange={setConfigKeys} style={{ marginBottom: 20 }} items={[
          { key: 'env', label: '环境变量与分布式参数', children: <><Alert type="info" title="auto随所选卡数生成；容器内GPU从0编号。MAX_PIXELS与IMAGE_MAX_TOKEN_NUM是否生效取决于镜像中的处理器。" style={{ marginBottom: 16 }} /><Row gutter={16}>{Object.keys(backend === 'k8s' && kind === 'wemm' ? {...catalog[kind]?.environment,...k8sInfo?.wemm_defaults?.environment} : catalog[kind]?.environment || {}).map(key => <Col xs={24} md={12} key={key}><Form.Item name={['environment', key]} label={envLabels[key] || key} extra={key}><Input /></Form.Item></Col>)}</Row></> },
          ...(backend === 'docker' ? [{ key: 'mounts', label: '附加数据挂载', children: <><Typography.Paragraph>训练数据所在目录、基础权重目录和输出目录自动挂载。JSONL引用其他图片目录时，在此补充；建议只读。</Typography.Paragraph><Form.List name="mounts">{(fields, { add, remove }) => <>{fields.map(field => <Row gutter={12} key={field.key}>
            <Col xs={24} md={9}><Form.Item name={[field.name, 'source']} label="服务器目录" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={9}><Form.Item name={[field.name, 'target']} label="容器目录" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={12} md={3}><Form.Item name={[field.name, 'read_only']} label="只读" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={12} md={3}><Button style={{ marginTop: 30 }} onClick={() => remove(field.name)}>移除</Button></Col>
          </Row>)}<Button onClick={() => add({ read_only: true })}>添加挂载</Button></>}</Form.List></> }] : []),
        ]} />
        </>}
        </> }]} />
        {saveError && <Alert type="error" role="alert" title={saveError} showIcon style={{ marginBottom: 12 }} />}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <Typography.Text type="secondary">待启动任务可修改；保存后须重新预检查，保存不会启动训练。</Typography.Text>
          <Space><Button disabled={saving} onClick={() => setOpen(false)}>取消</Button><Button type="primary" htmlType="submit" loading={saving}>保存任务配置</Button></Space>
        </div>
      </Form>
    </Modal>
    <Modal open={!!outputEdit} title="编辑导出 / 合并配置" width={1000} footer={null} onCancel={()=>setOutputEdit(undefined)}>
      {outputEdit&&<ModelOutputForm key={outputEdit.job.id} run={outputEdit.parent} job={outputEdit.job} servers={servers} admin={admin}
        onCreated={job=>{setOutputEdit(undefined);setSelected(job);setDetailTab('overview');setReload(n=>n+1);}}/>}
    </Modal>
    <Drawer open={!!selected && !open} size={1000} styles={{ wrapper: { maxWidth: '100vw' } }} title={selected?.name || '训练详情'} onClose={() => { if (!busy||outputBusy) {outputRequest.current++;outputController.current?.abort();outputController.current=null;setBusy('');setSelected(undefined);} }} closable={!busy||outputBusy} mask={{ closable: !busy||outputBusy }}>
      {selected && <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap>{status(selected)}{admin && <>
          <Button disabled={!!busy || selected.status !== 'draft'} onClick={() => {if(outputKinds.includes(selected.kind))void showOutputEdit(selected);else showEdit(selected);}}>编辑配置</Button>
          {!outputKinds.includes(selected.kind)&&<Button disabled={!!busy||(!catalog[selected.kind]&&!trainers.some(row=>row.id===selected.config.trainer_id))} onClick={()=>showEdit(selected,true)}>复制配置新建任务</Button>}
          <Button disabled={!!busy} onClick={() => { void action('precheck'); }}>预检查</Button>
          <Button type="primary" disabled={!!busy || selected.status !== 'draft' || checkResult?.ok !== true} onClick={() => setConfirm('start')}>{outputKinds.includes(selected.kind)?'启动执行':'启动训练'}</Button>
          <Button danger disabled={!!busy || !['draft', ...active].includes(selected.status)} onClick={() => setConfirm('stop')}>停止任务</Button>
          <Button disabled={!!busy} onClick={() => { void action('refresh'); }}>核对运行状态</Button>
          <Button danger disabled={!!busy||active.includes(selected.status)} onClick={()=>setConfirm('delete')}>删除任务</Button>
        </>}
          {selected.source.project_id&&<Link to={`/projects/${selected.source.project_id}/deliver`}>查看来源项目</Link>}
          {(canExport||canMerge)&&<Button disabled={!!busy} onClick={()=>setDetailTab('outputs')}>{canMerge?'合并LoRA模型':'导出模型'}</Button>}
          {admin&&selected.status==='completed'&&selected.kind==='model_merge'&&<Button disabled={!!busy} onClick={()=>void trainFromMerged(selected)}>使用合并模型继续训练</Button>}
          {admin&&canReuseFullModel&&<Button disabled={!!busy} onClick={()=>void readOutput('artifacts')}>选择完整模型继续训练</Button>}
          {admin&&selected.status==='completed'&&['model_merge','swift_sft','wemm'].includes(selected.kind)&&<Link to={`/model-deployments?run_id=${selected.id}`}>归档完整目录与更新模型 →</Link>}
          {admin&&selected.status==='completed'&&<Link to={`/model-repository?run_id=${selected.id}`}>存入模型仓库 →</Link>}
          {selected.config.execution_backend !== 'k8s'&&['yolo26','rfdetr_medium','rfdetr_2xlarge','dinov3_vits16_ltdetr'].includes(selected.kind)&&<Link to={selected.source.project_id ? `/projects/${selected.source.project_id}/test?view=validation&training_run_id=${encodeURIComponent(selected.id)}` : `/validation?training_run_id=${encodeURIComponent(selected.id)}`}>验证模型权重 →</Link>}
          {admin&&selected.config.execution_backend !== 'k8s'&&<Link to={`/gpu-terminal?server_id=${encodeURIComponent(selected.server.id)}`}>调试此服务器</Link>}
        </Space>
        {busy && <Space><Spin size="small" /><Typography.Text>{busy === 'start' ? '正在预检查并准备输入，可能需要几分钟；请勿重复启动。' : busy==='download'?'正在校验并下载产物，较大权重需要一些时间，请保持窗口打开…':busy==='delete'?'正在删除平台任务记录…':'正在读取服务器状态…'}</Typography.Text></Space>}
        {(detailError || selected.error) && <Alert type="error" showIcon title={detailError || selected.error} />}
        {selected.status === 'unknown' && <Alert type="warning" showIcon title="任务状态未知，资源仍被占用。请核对服务器上的任务状态。" />}
        {selected.config.onnx_stage&&<Alert type="info" showIcon title={`导出阶段：${({onnx:'所选服务器使用CPU导出ONNX',transferring:'校验并自动传输ONNX',tensorrt:'目标GPU服务器构建TensorRT'} as const)[selected.inspection.output_phase||'onnx']}`} description={`${selected.config.onnx_stage.server.name}（CPU） → ${selected.server.name}（GPU）；TensorRT允许共享有剩余显存的GPU，完成后在“模型产物”下载ONNX和engine。`}/>}
        <Tabs activeKey={detailTab} onChange={tab => { void readOutput(tab); }} items={[
          { key:'startup',label:'启动进度',children:<TrainingStartup run={selected}/> },
          ...(!outputKinds.includes(selected.kind)?[{key:'analysis',label:'效果分析与对比',children:<TrainingAnalysis key={selected.id} run={selected} admin={admin}/>}]:[]),
          { key: 'overview', label: '任务与配置', children: <><Descriptions column={1} bordered size="small" items={[
            { key: 'id', label: '任务ID', children: selected.id }, { key: 'kind', label: '训练器', children: selected.config.trainer?.name || outputNames[selected.kind] || catalog[selected.kind]?.name || selected.kind },
            { key: 'backend', label: '运行环境', children: selected.config.execution_backend === 'k8s' ? '嘉善移动K8s' : 'Docker' }, { key: 'server', label: '服务器 / 节点', children: selected.config.execution_backend === 'k8s' ? selected.config.k8s?.nodes.join('、') : selected.server.name }, { key: 'gpu', label: '执行设备', children: selected.config.execution_backend === 'k8s' ? `${selected.config.k8s?.nodes.length || 0} 节点 × ${selected.config.k8s?.gpus_per_node || 0} 卡` : selected.kind==='model_export'&&selected.config.output?.format==='onnx'?'CPU（不使用GPU）':selected.config.gpu_uuids.join(', ') },
            { key: 'source', label: '数据源 / 权重来源', children: selected.source.kind==='model_output'?`来源任务 ${selected.source.run_id} · ${selected.source.source_path}`:selected.source.kind === 'snapshot' ? `冻结导出 ${selected.source.id} · SHA256 ${selected.source.sha256}` : selected.config.dataset_path },
            { key: 'labels', label: '训练标签', children: selected.source.kind === 'remote' ? '沿用服务器配置' : selected.config.selected_labels?.join('、') || '全部标签' },
            { key: 'output', label: '产物目录', children: `${selected.output_dir}/${selected.config.output_subdir}` },
          ]} /><Typography.Title level={5}>保存的最终配置</Typography.Title><JsonView label="保存的最终配置" value={selected.config}/></> },
          { key: 'checks', label: '预检查', children: checkResult?.checks ? <Space orientation="vertical" style={{ width: '100%' }}>{checkResult.checks.map((check, i) => <Alert key={i} showIcon type={check.ok ? 'success' : 'error'} title={check.name} description={<JsonView label={check.name+'详情'} value={check.detail}/>} />)}</Space> : <Typography.Paragraph>保存任务后点击预检查；启动时会再次检查，避免使用过期资源状态。</Typography.Paragraph> },
          { key: 'logs', disabled:!!busy&&!outputBusy,label: outputKinds.includes(selected.kind)?'执行日志':'训练日志', children: <>{selected.config.execution_backend === 'k8s' && <Select aria-label="日志节点" disabled={!!busy&&!outputBusy} placeholder="选择日志节点" value={logNode} style={{width:300,marginBottom:12}} options={(selected.config.k8s?.nodes || []).map(node=>({value:node,label:node}))} onChange={node=>{setLogNode(node);void readOutput('logs',node);}}/>}<TrainingLogs key={selected.id} text={logs} busy={!!busy} onRefresh={()=>{void readOutput('logs');}}/></> },
          ...((canExport||canMerge)?[{key:'outputs',label:canMerge?'LoRA合并':'模型导出',children:<ModelOutputForm key={selected.id} run={selected} servers={servers} admin={admin} initialSource={outputPath} onCreated={job=>{void inspect(job);setReload(n=>n+1);}}/>}]:[]),
          { key: 'artifacts', disabled:!!busy&&!outputBusy,label: '模型产物', children: <><Space wrap style={{marginBottom:12}}><Button aria-label="刷新模型产物" loading={busy==='artifacts'} disabled={!!busy&&!outputBusy} onClick={()=>{void readOutput('artifacts');}}>刷新产物</Button>{admin&&selected.status==='completed'&&mergeSources.includes(selected.kind)&&<Button disabled={!!busy} onClick={()=>void archiveAndDownload('base/config.json')}>下载基础模型并入库</Button>}</Space>{truncated && <Alert type="warning" showIcon title="仅显示前500个文件，完整产物保留在服务器目录。" style={{marginBottom:12}} />}<Table rowKey="path" scroll={{ x: 650 }} dataSource={files} pagination={{ pageSize: 20 }} columns={[{ title: '文件', dataIndex: 'path' }, { title: '大小', dataIndex: 'bytes',render:value=>value<1024?`${value} B`:`${(value/1024**2).toFixed(2)} MiB` }, { title: '类型', dataIndex: 'role', render: value => value === 'weights' ? '模型权重' : '配置/日志' },{title:'操作',render:(_,file)=><Space size="small" wrap><Button size="small" disabled={!!busy} onClick={()=>{void download(file);}}>下载到本地</Button>{admin&&selected.status==='completed'&&(mergeSources.includes(selected.kind)||selected.kind==='model_merge')&&/(^|\/)(adapter_)?config\.json$/.test(file.path)&&<Button size="small" disabled={!!busy} onClick={()=>void archiveAndDownload(file.path)}>下载模型目录并入库</Button>}{admin&&canReuseFullModel&&/(^|\/)config\.json$/.test(file.path)&&<Button size="small" disabled={!!busy} onClick={()=>showEdit(selected,true,(`${selected.output_dir}/${selected.config.output_subdir}/${file.path}`).replace(/\/config\.json$/,""))}>用此模型继续训练</Button>}{admin&&canExport&&/\.(pt|pth)$/.test(file.path)&&<Button size="small" disabled={!!busy} onClick={()=>{setOutputPath(`${selected.output_dir}/${selected.config.output_subdir}/${file.path}`);setDetailTab('outputs');}}>导出此权重</Button>}</Space>}]} /><Typography.Paragraph type="secondary">可下载模型权重、配置和日志；训练中的文件如发生变化会拒绝本次下载，请选稳定检查点或结束后重试。</Typography.Paragraph></> },
        ]} />
      </Space>}
    </Drawer>
    <Modal open={!!confirm} title={confirm === 'start' ? (selected&&outputKinds.includes(selected.kind)?'启动此导出 / 合并任务？':'启动此训练任务？') : confirm==='delete'?'删除此任务记录？':'停止此任务？'} confirmLoading={!!busy} okText={confirm === 'start' ? '确认启动' : confirm==='delete'?'确认删除':'确认停止'} cancelText="返回" okButtonProps={{ danger: confirm !== 'start' }} onCancel={() => { if (!busy) setConfirm(undefined); }} onOk={() => { if (confirm) void action(confirm); }}>
      <Typography.Paragraph>{confirm === 'start' ? (selected&&outputKinds.includes(selected.kind)?'将重新检查来源、镜像和GPU，创建独立导出/合并容器，占用所选GPU；输入只读，结果写入新任务目录。':selected?.config.execution_backend === 'k8s' ? '将重新检查镜像、数据和节点GPU，然后创建K8s训练任务。' : '将重新检查镜像、数据和GPU，然后创建独立Docker容器。不会停止其他服务。冻结包需先传输并校验，训练会占用所选GPU。') : confirm==='delete'?'只移除平台任务记录。服务器上的模型权重、日志、数据和容器均保留；删除后该任务入口消失，请先下载所需产物。已有验证记录保留。':selected?.config.execution_backend === 'k8s' ? '只停止此K8s训练任务，保留数据、日志和已保存产物。' : '只停止本平台记录的对应任务容器，保留数据、日志和已保存产物；确认停止后释放GPU占用。'}</Typography.Paragraph>
      {detailError && <Alert type="error" showIcon title={detailError} />}
    </Modal>
  </Space>;
}
