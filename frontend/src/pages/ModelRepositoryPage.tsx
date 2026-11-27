import { useEffect, useRef, useState } from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { Alert, Badge, Button, Card, Checkbox, Descriptions, Drawer, Dropdown, Empty, Form, Input, InputNumber, Modal, Pagination, Popover, Progress, Select, Space, Spin, Table, Tag, Typography, Upload } from 'antd';
import { CopyOutlined, DownloadOutlined, EllipsisOutlined, RightOutlined, UploadOutlined } from '@ant-design/icons';
import type { ReactNode } from 'react';
import { useCurrentUser } from '../components/currentUser';
import { getTrainingRun } from '../api/training';
import { downloadRepositoryBatch, downloadRepositoryModel, getRepositoryArtifacts, getRepositoryConfig, getRepositoryGroups, getRepositoryModels, getRepositorySources, importRepositoryModel, repositoryBatchLabel, repositoryConfigPath, repositoryDownloadCommand, retryRepositoryModel, updateRepositoryModel, uploadRepositoryModel } from '../api/modelRepository';
import type { RepositoryArtifact, RepositoryDownloadFilters, RepositoryFormat, RepositoryGroup, RepositoryMetadata, RepositoryModel, RepositorySource, RepositoryUpdate } from '../api/modelRepository';
import { deploymentError } from '../utils/modelDeployment';
import { appMessage, appModal } from '../ui/appFeedback';
import './ModelRepositoryPage.css';

const formats = [{ value: 'pt', label: 'PT' }, { value: 'onnx', label: 'ONNX' }, { value: 'tensorrt', label: 'TensorRT' }, { value:'python',label:'Python backend' }, {value:'huggingface',label:'Hugging Face 完整模型'}];
const statusNames = { importing: '正在入库', ready: '可下载', failed: '入库失败', interrupted: '入库中断' };
const extensions = { pt: /\.(pt|pth)$/i, onnx: /\.onnx$/i, tensorrt: /\.(engine|plan)$/i, python: /(^|\/)\d+\/model\.py$/, huggingface: /^merged\/config\.json$/ };
const bytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(2)} KiB` : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(2)} MiB` : `${(value / 1024 ** 3).toFixed(2)} GiB`;
const filePath = (file: File) => file.webkitRelativePath || file.name;
type ImportForm = RepositoryMetadata & { entry_file: string; run_id: string; artifact_path: string; batch_mode:''|'fixed'|'dynamic'; batch_size:number; min_batch:number; opt_batch:number; max_batch:number };
type DownloadForm = RepositoryDownloadFilters & { mode: 'selected' | 'filtered' };

function RepositoryVersions({ group, q, format, gpu, reload, renderModel }: { group:string; q:string; format?:RepositoryFormat; gpu?:string; reload:number; renderModel:(model:RepositoryModel)=>ReactNode }) {
  const [models,setModels]=useState<RepositoryModel[]>([]),[total,setTotal]=useState(0),[page,setPage]=useState(1),[loading,setLoading]=useState(true),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError('');
    getRepositoryModels({page,q:q||undefined,format,gpu_model:gpu,group_name:group},controller.signal)
      .then(result=>{setModels(result.items);setTotal(result.total);})
      .catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[group,page,q,format,gpu,reload,retry]);
  return <div className="repository-versions">{error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>setRetry(value=>value+1)}>重试版本</Button>} />}
    <Spin spinning={loading}><div className="repository-card-grid">{models.map(renderModel)}</div>{!loading&&!models.length&&<Empty description="暂无模型版本" />}</Spin>
    <div style={{marginTop:20,display:'flex',justifyContent:'flex-end'}}>
      <Pagination current={page} pageSize={20} total={total} showSizeChanger={false} onChange={setPage} showTotal={value=>`共 ${value} 个模型版本`} />
    </div>
  </div>;
}

export default function ModelRepositoryPage() {
  const admin = useCurrentUser()?.role === 'admin';
  const [params] = useSearchParams();
  const [form] = Form.useForm<ImportForm>();
  const [editForm] = Form.useForm<RepositoryUpdate>();
  const [downloadForm]=Form.useForm<DownloadForm>();
  const downloadMode=Form.useWatch('mode',downloadForm);
  const [selectedIds,setSelectedIds]=useState<string[]>([]),[downloadOpen,setDownloadOpen]=useState(false),[batchDownloading,setBatchDownloading]=useState(false),[downloadError,setDownloadError]=useState('');
  const selectedRun = Form.useWatch('run_id', form), selectedFormat = Form.useWatch('format', form) as RepositoryFormat | undefined;
  const selectedBatchMode=Form.useWatch('batch_mode',form),selectedBatchProfile=Form.useWatch('batch_profile',form);
  const [groups, setGroups] = useState<RepositoryGroup[]>([]), [total, setTotal] = useState(0), [versionTotal,setVersionTotal]=useState(0), [gpuModels, setGpuModels] = useState<string[]>([]);
  const [page, setPage] = useState(1), [q, setQ] = useState(''), [format, setFormat] = useState<RepositoryFormat>(), [gpu, setGpu] = useState<string>();
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState(''), [reload, setReload] = useState(0);
  const [mode, setMode] = useState<'upload' | 'training'>(), [saving, setSaving] = useState(false), [saveError, setSaveError] = useState(''), [progress, setProgress] = useState(0);
  const [files, setFiles] = useState<File[]>([]), [sources, setSources] = useState<RepositorySource[]>([]), [sourcePage, setSourcePage] = useState(1), [sourceMore, setSourceMore] = useState(false), [sourceLoading, setSourceLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<RepositoryArtifact[]>([]), [artifactLoading, setArtifactLoading] = useState(false), [truncated, setTruncated] = useState(false);
  const [openGroup,setOpenGroup]=useState<string>();
  const versionDrawer = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<RepositoryModel>(), [busyId, setBusyId] = useState('');
  const [downloadingIds, setDownloadingIds] = useState<string[]>([]);
  const [editing, setEditing] = useState(false), [editSaving, setEditSaving] = useState(false), [editError, setEditError] = useState('');
  const buildEditable=selected?.format==='tensorrt'&&selected.source?.kind==='upload'&&!selected.run_id;
  const [configModel,setConfigModel]=useState<RepositoryModel>(),[configContent,setConfigContent]=useState(''),[configLoading,setConfigLoading]=useState(false),[configError,setConfigError]=useState('');
  const refresh = () => setReload(value => value + 1);

  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    getRepositoryGroups({ page, q: q || undefined, format, gpu_model: gpu }, controller.signal).then(result => {
      setGroups(result.items); setTotal(result.total); setVersionTotal(result.version_total); setGpuModels(result.gpu_models);
    }).catch(cause => { if (!controller.signal.aborted) setError(deploymentError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, q, format, gpu, reload]);
  useEffect(() => {
    if (!groups.some(row => row.importing_count > 0)) return;
    const timer = setInterval(refresh, 5000); return () => clearInterval(timer);
  }, [groups]);
  useEffect(()=>{
    if(!configModel)return;
    const controller=new AbortController();setConfigLoading(true);setConfigError('');setConfigContent('');
    getRepositoryConfig(configModel.id,controller.signal).then(result=>setConfigContent(result.content))
      .catch(cause=>{if(!controller.signal.aborted)setConfigError(deploymentError(cause));})
      .finally(()=>{if(!controller.signal.aborted)setConfigLoading(false);});
    return ()=>controller.abort();
  },[configModel]);
  useEffect(() => {
    if (!admin || mode !== 'training') return;
    const controller = new AbortController(); setSourceLoading(true);
    getRepositorySources(sourcePage, controller.signal).then(result => {
      setSources(values => [...new Map([...values, ...result.items].map(row => [row.id, row])).values()]);
      setSourceMore(sourcePage * 20 < result.total);
    }).catch(cause => { if (!controller.signal.aborted) setSaveError(deploymentError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setSourceLoading(false); });
    return () => controller.abort();
  }, [admin, mode, sourcePage]);
  useEffect(() => {
    if (!admin || mode !== 'training' || !selectedRun) return;
    const controller = new AbortController(); setArtifactLoading(true); setArtifacts([]); setTruncated(false);
    getRepositoryArtifacts(selectedRun, controller.signal).then(result => { setArtifacts(result.items); setTruncated(result.truncated); })
      .catch(cause => { if (!controller.signal.aborted) setSaveError(deploymentError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setArtifactLoading(false); });
    return () => controller.abort();
  }, [admin, mode, selectedRun]);
  useEffect(() => {
    const id = params.get('run_id'); if (!admin || !id) return;
    let live = true;
    getTrainingRun(id).then(run => {
      if (!live) return;
      if (run.status !== 'completed') { setError('仅已完成任务的模型产物可入库。'); return; }
      setSources(values => values.some(row => row.id === id) ? values : [{ id, name: run.name, kind: run.kind, created_at: run.created_at }, ...values]);
      form.setFieldsValue({ run_id: id, name: run.name }); setMode('training');
    }).catch(cause => { if (live) setError(deploymentError(cause)); });
    return () => { live = false; };
  }, [admin, params, form]);

  const openImport = (next: 'upload' | 'training') => {
    form.resetFields(); setFiles([]); setArtifacts([]); setTruncated(false); setSaveError(''); setProgress(0); setMode(next);
  };
  const save = async (values: ImportForm) => {
    if (saving || !mode) return;
    if (mode === 'upload' && !files.length) { setSaveError('请选择模型文件。'); return; }
    if (mode === 'upload' && new Set(files.map(filePath)).size !== files.length) { setSaveError('文件路径重复，请移除重名文件后重新选择。'); return; }
    if (mode === 'upload' && values.format === 'tensorrt' && files.some(file => filePath(file) === 'config.pbtxt' && file.size > 1024 ** 2)) { setSaveError('config.pbtxt不能超过1 MiB'); return; }
    if (mode==='upload' && values.format==='tensorrt' && values.batch_mode==='dynamic' && !(values.min_batch<=values.opt_batch && values.opt_batch<=values.max_batch)) { setSaveError('动态batch必须满足 min ≤ opt ≤ max');return; }
    setSaving(true); setSaveError('');
    try {
      if (mode === 'upload') await uploadRepositoryModel({ name: values.name.trim(), group_name:values.group_name?.trim(), version: values.version.trim(), format: values.format, entry_file: values.entry_file,
        ...(values.format === 'tensorrt' ? { gpu_model: values.gpu_model?.trim(), tensorrt_version: values.tensorrt_version?.trim(),batch_profile:values.batch_mode==='fixed'?`fixed:${values.batch_size}`:values.batch_mode==='dynamic'?`dynamic:${values.min_batch}:${values.opt_batch}:${values.max_batch}`:'' } : {}) }, files, setProgress);
      else await importRepositoryModel({ name: values.name.trim(), group_name:values.group_name?.trim(), version: values.version.trim(), format: values.format, run_id: values.run_id, artifact_path: values.artifact_path });
      setNotice(mode === 'upload' ? '模型已上传并保存。' : '已提交后台入库，文件复制和校验完成后可下载。');
      setMode(undefined); setPage(1); refresh();
    } catch (cause) { setSaveError(deploymentError(cause)); }
    finally { setSaving(false); }
  };
  const download = async (row: RepositoryModel) => {
    if (downloadingIds.includes(row.id)) return;
    setDownloadingIds(ids => [...ids, row.id]); setError('');
    try { await downloadRepositoryModel(row); }
    catch (cause) { setError(deploymentError(cause)); }
    finally { setDownloadingIds(ids => ids.filter(id => id !== row.id)); }
  };
  const openBatchDownload=(group_name?:string)=>{
    downloadForm.resetFields();downloadForm.setFieldsValue({mode:selectedIds.length?'selected':'filtered',q,format,gpu_model:gpu,group_name});
    setDownloadError('');setDownloadOpen(true);
  };
  const batchDownload=async(values:DownloadForm)=>{
    if(batchDownloading)return;
    if(values.mode==='selected'&&!selectedIds.length){setDownloadError('请先勾选模型版本，或切换为按筛选条件下载。');return;}
    setBatchDownloading(true);setDownloadError('');
    try{
      await downloadRepositoryBatch(values.mode==='selected'?{ids:selectedIds}:{filters:{q:values.q?.trim()||undefined,group_name:values.group_name?.trim()||undefined,format:values.format,gpu_model:values.gpu_model}});
      setDownloadOpen(false);setNotice('批量下载已开始，ZIP中包含各版本独立目录及manifest.json清单。');
    }catch(cause){setDownloadError(deploymentError(cause));}finally{setBatchDownloading(false);}
  };
  const toggleVersion=(row:RepositoryModel,checked:boolean)=>{
    if(checked&&selectedIds.length>=100){setError('一次最多勾选100个版本，请缩小范围。');return;}
    setSelectedIds(previous=>checked?[...new Set([...previous,row.id])]:previous.filter(id=>id!==row.id));
  };
  const retry = async (row: RepositoryModel) => {
    setBusyId(row.id); setError('');
    try { await retryRepositoryModel(row.id); refresh(); }
    catch (cause) { setError(deploymentError(cause)); }
    finally { setBusyId(''); }
  };
  const openDetails = (row: RepositoryModel, edit = false) => {
    setSelected(row); setEditing(edit); setEditError('');
    if (edit) { editForm.resetFields(); editForm.setFieldsValue({ name: row.name, group_name:row.group_name||'', version: row.version, description: row.description || '',gpu_model:row.gpu_model,tensorrt_version:row.tensorrt_version||'' }); }
  };
  const saveEdit = async (values: RepositoryUpdate) => {
    if (!selected || editSaving) return;
    setEditSaving(true); setEditError('');
    try {
      await updateRepositoryModel(selected.id, { name: values.name.trim(), group_name:values.group_name?.trim()||'', version: values.version.trim(), description: values.description.trim(),
        ...(buildEditable?{gpu_model:values.gpu_model?.trim(),tensorrt_version:values.tensorrt_version?.trim()||''}:{}) });
      setSelected(undefined); setNotice('模型版本信息已更新。'); refresh();
    } catch (cause) { setEditError(deploymentError(cause)); }
    finally { setEditSaving(false); }
  };
  const copyDownload = async (row: RepositoryModel) => {
    let command: string;
    try { command = await repositoryDownloadCommand(row); }
    catch (cause) { appMessage.error(deploymentError(cause)); return; }
    if (navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(command);
        appMessage.success('curl 下载命令已复制。');
        return;
      } catch { /* Try selecting the address inside the open drawer. */ }
    }
    const container = versionDrawer.current;
    if (container) {
      const input = document.createElement('textarea');
      input.value = command;
      input.readOnly = true;
      input.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      container.appendChild(input);
      try {
        input.focus({ preventScroll: true });
        input.select();
        if (document.activeElement === input && document.execCommand('copy')) {
          appMessage.success('curl 下载命令已复制。');
          return;
        }
      } catch { /* The browser can block both clipboard methods. */ }
      finally { input.remove(); }
    }
    appModal.info({
      title: '请手动复制 curl 下载命令',
      content: <Input.TextArea aria-label="curl 下载命令" value={command} readOnly rows={4} onFocus={event => event.target.select()} />,
      okText: '关闭',
    });
  };

  const renderVersion=(row:RepositoryModel)=><Card key={row.id} className="repository-version-card" role="article" aria-label={`${row.name} ${row.version}`}>
    <Checkbox aria-label={`勾选 ${row.name} ${row.version}`} checked={selectedIds.includes(row.id)} disabled={row.status!=='ready'||batchDownloading} onChange={event=>toggleVersion(row,event.target.checked)}>批量下载</Checkbox>
    <div className="repository-model"><div className="repository-model-title"><button className="repository-name" onClick={() => openDetails(row)}>{row.name}</button><Tag>{row.version}</Tag></div><span className="repository-file-name" title={row.entry_file}>{row.entry_file}</span>{row.description && <p className="repository-note" title={row.description}>{row.description.replace(/\s+/g, ' ')}</p>}{row.error && <p className="repository-row-error">{row.error}</p>}</div>
    <div className="repository-cell"><Tag color={row.format === 'tensorrt' ? 'blue' : 'default'}>{formats.find(item => item.value === row.format)?.label}</Tag>{row.format === 'tensorrt' ? <><span>{row.gpu_model}</span><small>TensorRT {row.tensorrt_version || '未记录'}</small><small>{repositoryBatchLabel(row.batch_profile)}</small></> : <small>{row.format==='python'?'完整目录；需目标环境及依赖模型':'不限定 GPU 型号'}</small>}{row.source?.onnx_input && <small>输入 {row.source.onnx_input.shape.join(' × ')}</small>}</div>
    <div className="repository-cell"><span>{bytes(row.file_size)} <small>· {row.files.length} 个文件</small></span><Badge status={row.status === 'ready' ? 'success' : row.status === 'importing' ? 'processing' : 'error'} text={statusNames[row.status]} /><Button className="repository-file-link" type="link" size="small" onClick={() => openDetails(row)}>文件与 SHA256</Button>{['tensorrt','python'].includes(row.format) && (row.files.some(file=>file.path===repositoryConfigPath(row)) ? <Button className="repository-file-link" type="link" size="small" disabled={row.status!=='ready'} onClick={()=>setConfigModel(row)}>查看 config.pbtxt</Button> : <small>未附带 config.pbtxt</small>)}</div>
    <div className="repository-cell"><span>{row.source?.kind === 'training' || row.run_id ? '训练产物' : '直接上传'}</span><small>{new Date(row.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false })}</small></div>
    <Space size={2}><Button aria-label={row.files.length > 1 ? '下载 ZIP' : '下载'} icon={<DownloadOutlined />} disabled={row.status !== 'ready' || downloadingIds.includes(row.id)} loading={downloadingIds.includes(row.id)} onClick={() => void download(row)}>下载{row.files.length > 1 ? ' ZIP' : ''}</Button>
            {admin && <Button type="text" aria-label="编辑" disabled={row.status === 'importing'} onClick={() => openDetails(row, true)}>编辑</Button>}
            {row.status === 'ready' && <Dropdown trigger={['click']} menu={{ items:[{ key:'copy',icon:<CopyOutlined aria-hidden="true" />,label:'复制 curl 下载命令',onClick:()=>void copyDownload(row) },...(admin && row.format === 'tensorrt' ? [{ key:'deploy',label:<Link to={`/model-deployments?repository_id=${row.id}`}>选择服务器更新</Link> }] : [])] }}><Button type="text" aria-label="更多操作" icon={<EllipsisOutlined />} /></Dropdown>}
            {admin && row.run_id && ['failed', 'interrupted'].includes(row.status) && <Button size="small" disabled={!!busyId} onClick={() => void retry(row)}>重试入库</Button>}</Space>
  </Card>;


  return <Space className="model-repository" orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {error && openGroup===undefined && <Alert type="error" showIcon closable onClose={() => setError('')} title={error} />}
    {notice && <Alert type="success" showIcon closable onClose={() => setNotice('')} title={notice} />}
    <Card className="repository-list">
      <div className="repository-list-heading"><div><h2>模型分组 <span>{total} 组 · {versionTotal} 个版本</span></h2><p>按模型组集中管理组件，展开查看 PT、ONNX、TensorRT 和 Python backend 版本。</p></div>
        <Popover trigger="click" title="下载接口说明" content={<p className="repository-api-help">在模型版本的更多操作中复制 curl 下载命令，可直接在终端执行。命令里的授权仅适用于所选模型版本，2 小时内有效。</p>}><Button type="text">下载接口说明</Button></Popover>
      </div>
      <div className="page-toolbar">
        <Space wrap>
          <Input.Search aria-label="搜索模型名称" placeholder="搜索模型名称或模型组" allowClear style={{ width: 240, maxWidth: '100%' }} onSearch={value => { setQ(value.trim()); setPage(1); }} />
          <Select<RepositoryFormat> aria-label="筛选模型格式" placeholder="全部格式" allowClear value={format} style={{ width: 140 }} options={formats} onChange={value => { setFormat(value); setPage(1); }} />
          <Select aria-label="筛选GPU型号" placeholder="全部 GPU 型号" allowClear showSearch value={gpu} style={{ width: 220 }} options={gpuModels.map(value => ({ value, label: value }))} onChange={value => { setGpu(value); setPage(1); }} />
        </Space>
        <Space wrap><Button icon={<DownloadOutlined />} disabled={batchDownloading} onClick={()=>openBatchDownload()}>批量下载{selectedIds.length?`（${selectedIds.length}）`:''}</Button><Button onClick={refresh} loading={loading}>刷新</Button>{admin && <><Button onClick={() => openImport('training')}>从训练产物入库</Button><Button type="primary" icon={<UploadOutlined />} onClick={() => openImport('upload')}>上传模型</Button></>}</Space>
      </div>
      {!!selectedIds.length&&<Space wrap style={{marginBottom:12}}><Typography.Text>已勾选 {selectedIds.length} 个版本（跨分组和分页保留）</Typography.Text><Button type="link" size="small" onClick={()=>setSelectedIds([])}>清空勾选</Button></Space>}
      <Spin spinning={loading}>
        <div className="repository-card-grid">{groups.map(group=><Card key={group.name} className="repository-group-card" role="article" aria-label={group.name}
          title={group.name} extra={<Tag>{group.version_count} 个版本</Tag>}>
          <Space wrap>{group.formats.map(value=><Tag key={value}>{formats.find(item=>item.value===value)?.label}</Tag>)}</Space>
          <p>{group.gpu_models.join(' / ')||'不限定 GPU 型号'}</p>
          <p className="repository-card-time">最近入库 {new Date(group.latest_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</p>
          <Button icon={<RightOutlined />} onClick={()=>setOpenGroup(group.name)}>查看模型版本</Button>
        </Card>)}</div>
        {!loading&&!groups.length&&<Empty description={q||format||gpu?'没有符合条件的模型':'模型仓库暂无文件'} />}
      </Spin>
      <div style={{marginTop:20,display:'flex',justifyContent:'flex-end'}}>
        <Pagination current={page} pageSize={20} total={total} showSizeChanger={false} onChange={setPage} showTotal={value=>`共 ${value} 组模型`} />
      </div>
      <Drawer className="repository-drawer" title={openGroup} open={openGroup!==undefined} size="90vw" styles={{wrapper:{maxWidth:'100vw'}}} destroyOnHidden onClose={()=>setOpenGroup(undefined)}
        extra={<Button icon={<DownloadOutlined />} disabled={batchDownloading} onClick={()=>openBatchDownload(openGroup)}>批量下载{selectedIds.length?`（${selectedIds.length}）`:''}</Button>}>
        <div ref={versionDrawer}>
          {error&&<Alert type="error" showIcon closable onClose={()=>setError('')} title={error} />}
          {openGroup!==undefined&&<RepositoryVersions key={`${openGroup}/${q}/${format}/${gpu}`} group={openGroup} q={q} format={format} gpu={gpu} reload={reload} renderModel={renderVersion} />}
        </div>
      </Drawer>
    </Card>
    <Modal open={downloadOpen} title="批量下载模型" width={620} footer={null} closable={!batchDownloading} mask={{closable:!batchDownloading}} onCancel={()=>{if(!batchDownloading)setDownloadOpen(false);}}>
      <Form form={downloadForm} name="repository-download" layout="vertical" disabled={batchDownloading} onFinish={values=>void batchDownload(values)}>
        <Form.Item name="mode" label="下载方式" rules={[{required:true}]}><Select options={[{value:'selected',label:`下载已勾选版本（${selectedIds.length}）`},{value:'filtered',label:'按筛选条件下载全部匹配版本'}]}/></Form.Item>
        {downloadMode==='filtered'&&<>
          <Form.Item name="q" label="模型名称或模型组关键词" rules={[{max:100}]}><Input maxLength={100} allowClear/></Form.Item>
          <Form.Item name="group_name" label="模型组（精确匹配，可选）" rules={[{max:100}]}><Input maxLength={100} placeholder="例如 sam3，留空匹配全部组" allowClear/></Form.Item>
          <Form.Item name="format" label="模型格式"><Select allowClear placeholder="全部格式" options={formats}/></Form.Item>
          <Form.Item name="gpu_model" label="GPU 型号"><Select allowClear showSearch placeholder="全部GPU型号" options={gpuModels.map(value=>({value,label:value}))}/></Form.Item>
        </>}
        <Typography.Paragraph type="secondary">每批最多100个版本。筛选下载包含所有分页中的可下载版本，不含入库中或失败的记录。ZIP按版本分目录保存，模型文件和Python backend目录结构均保留；大文件建议通过接口在服务器下载。</Typography.Paragraph>
        {downloadError&&<Alert type="error" showIcon title={downloadError} style={{marginBottom:16}}/>}
        {batchDownloading&&<Typography.Paragraph>正在校验、打包并下载，请保持页面打开。</Typography.Paragraph>}
        <div style={{display:'flex',justifyContent:'flex-end'}}>
          <Space><Button disabled={batchDownloading} onClick={()=>setDownloadOpen(false)}>取消</Button><Button type="primary" htmlType="submit" loading={batchDownloading}>开始下载</Button></Space>
        </div>
      </Form>
    </Modal>
    <Modal open={!!mode} title={mode === 'upload' ? '上传模型到仓库' : '从已完成训练产物入库'} width={720} footer={null} closable={!saving} mask={{ closable: !saving }} onCancel={() => { if (!saving) setMode(undefined); }}>
      <Form form={form} name="model-repository-import" layout="vertical" disabled={saving} initialValues={{ format: 'pt',batch_mode:'',batch_size:1,min_batch:1,opt_batch:1,max_batch:8 }} onFinish={values => void save(values)}>
        {mode === 'training' && <>
          <Form.Item name="run_id" label="已完成任务" rules={[{ required: true, message: '请选择已完成任务' }]}><Select showSearch loading={sourceLoading} optionFilterProp="label" placeholder="选择训练或导出任务" options={sources.map(row => ({ value: row.id, label: `${row.name} · ${row.kind}` }))} onChange={id => { form.setFieldsValue({ name: sources.find(row => row.id === id)?.name, artifact_path: undefined, gpu_model: undefined, batch_profile: undefined }); setSaveError(''); }} /></Form.Item>
          {sourceMore && <Button loading={sourceLoading} onClick={() => setSourcePage(value => value + 1)} style={{ marginBottom: 16 }}>加载更多已完成任务</Button>}
          <Form.Item name="artifact_path" label="模型产物" rules={[{ required: true, message: '请选择模型产物' }]} extra="仅显示可入库的 PT、ONNX、TensorRT；ONNX 配套数据由平台一起收集。"><Select loading={artifactLoading} disabled={!selectedRun || artifactLoading || saving} options={artifacts.map(row => ({ value: row.path, label: `${row.path} · ${bytes(row.bytes)}${row.gpu_model ? ` · ${row.gpu_model}` : ''}` }))} notFoundContent={artifactLoading ? '正在读取产物…' : '未找到可用模型产物'} onChange={path => { const artifact = artifacts.find(row => row.path === path); if (artifact) form.setFieldsValue({ format: artifact.format, gpu_model: artifact.gpu_model, batch_profile:artifact.batch_profile||'' }); }} /></Form.Item>
          {truncated && <Alert type="warning" showIcon title="产物列表已截断，更多文件仍保留在训练服务器。" style={{ marginBottom: 16 }} />}
        </>}
        <Form.Item name="name" label="模型名称" rules={[{ required: true, whitespace: true, max: 100, message: '请输入模型名称（最多100字）' }]}><Input /></Form.Item>
        <Form.Item name="group_name" label="模型组（可选）" rules={[{max:100,message:'模型组最多100字'}]} extra="同组组件统一填写，例如sam3；留空沿用按模型名称自动分组。"><Input maxLength={100} placeholder="例如 sam3" /></Form.Item>
        <Form.Item name="version" label="版本号" rules={[{ required: true, whitespace: true, max: 100, message: '请输入版本号（最多100字）' }]} extra="例如 v1.0；同一模型、格式及 GPU 型号下版本不可重复。"><Input /></Form.Item>
        <Form.Item name="format" label="模型格式" rules={[{ required: true }]}><Select disabled={mode === 'training' || saving} options={formats.filter(item=>item.value!==(mode==='training'?'python':'huggingface'))} onChange={() => form.setFieldValue('entry_file', undefined)} /></Form.Item>
        {selectedFormat==='python'&&<Alert type="info" showIcon title="上传完整Python backend目录" description="选择包含config.pbtxt和数字版本目录（如1/model.py）的模型文件夹，tokenizer、npy和其他依赖文件会按原相对路径保存；下载为ZIP。入库不执行代码，此入口不直接下发Python目录。" style={{marginBottom:16}}/>}
        {selectedFormat === 'tensorrt' && <>
          <Form.Item name="gpu_model" label="GPU 型号" rules={mode === 'upload' ? [{ required: true, whitespace: true, max: 200, message: '请输入构建此模型的 GPU 型号' }] : []} extra={mode === 'upload' ? '填写构建此 engine / plan 的实际 GPU 型号，例如 NVIDIA T4；型号相同仍需确认运行环境兼容。' : 'GPU 型号以训练产物的构建清单为准。'}><Input disabled={mode === 'training' || saving} /></Form.Item>
          {mode === 'upload' && <Form.Item name="tensorrt_version" label="TensorRT 版本（可选）" extra="通过平台下发时必须提供构建版本，用于核对目标推理环境。"><Input placeholder="例如 10.8.0" /></Form.Item>}
          {mode==='training'?<Form.Item label="Batch 规格"><Typography.Text>{repositoryBatchLabel(selectedBatchProfile)}</Typography.Text></Form.Item>:<>
            <Form.Item name="batch_mode" label="Batch 类型" extra="填写构建此 engine 时的实际规格；未填写的历史模型保留为未记录。"><Select options={[{value:'',label:'未记录'},{value:'fixed',label:'固定 batch'},{value:'dynamic',label:'动态 batch'}]} /></Form.Item>
            {selectedBatchMode==='fixed'&&<Form.Item name="batch_size" label="固定 batch" rules={[{required:true,message:'请输入固定 batch'}]}><InputNumber min={1} max={1024} /></Form.Item>}
            {selectedBatchMode==='dynamic'&&<Space wrap align="start">{(['min_batch','opt_batch','max_batch'] as const).map((key,index)=><Form.Item key={key} name={key} label={['最小 batch','最佳 batch','最大 batch'][index]} rules={[{required:true,message:'请输入 batch'}]}><InputNumber min={1} max={1024}/></Form.Item>)}</Space>}
          </>}
        </>}
        {mode === 'upload' && <>
          <Form.Item label="模型文件及关联数据" required extra={selectedFormat==='tensorrt' ? '选择 engine / plan 主文件，可同时选择根目录的 config.pbtxt（UTF-8，最多1 MiB）。配置会随模型下载，下发继续使用目标服务配置。' : '保留原文件名；ONNX 外部权重请与主文件一并上传。平台仅存储校验，不执行模型验证。可选择文件夹保留相对目录。'}>
            <Space wrap>{selectedFormat!=='python'&&<Upload multiple showUploadList={false} beforeUpload={file => { setFiles(values => [...values, file]); return false; }}><Button icon={<UploadOutlined />}>选择文件</Button></Upload>}
              <Upload directory multiple showUploadList={false} beforeUpload={file => { setFiles(values => [...values, file]); return false; }}><Button>选择文件夹</Button></Upload></Space>
            {files.length > 0 && <ul style={{ paddingLeft: 20, maxHeight: 180, overflow: 'auto' }}>{files.map((file, index) => <li key={`${filePath(file)}-${index}`} style={{ overflowWrap: 'anywhere' }}>{filePath(file)} · {bytes(file.size)} <Button size="small" type="link" disabled={saving} onClick={() => { setFiles(values => values.filter((_, i) => i !== index)); if (form.getFieldValue('entry_file') === filePath(file)) form.setFieldValue('entry_file', undefined); }}>移除</Button></li>)}</ul>}
          </Form.Item>
          <Form.Item name="entry_file" label="主模型文件" rules={[{ required: true, message: '请选择主模型文件' }]}><Select placeholder="从已选文件中选择模型入口" options={files.filter(file => selectedFormat && extensions[selectedFormat].test(filePath(file))).map(file => ({ value: filePath(file), label: filePath(file) }))} /></Form.Item>
        </>}
        {saving && mode === 'upload' && <Progress percent={progress} status="active" format={value => value === 100 ? '正在校验' : `${value}%`} />}
        {saving && mode === 'training' && <Typography.Paragraph type="secondary">正在核对来源文件，完成后后台复制。</Typography.Paragraph>}
        {saveError && <Alert type="error" role="alert" showIcon title={saveError} style={{ marginBottom: 16 }} />}
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Space><Button disabled={saving} onClick={() => setMode(undefined)}>取消</Button><Button type="primary" htmlType="submit" loading={saving}>{mode === 'upload' ? '上传并保存' : '开始入库'}</Button></Space>
        </div>
      </Form>
    </Modal>
    <Drawer className="repository-drawer" open={!!selected} title={editing ? '版本配置' : '版本详情'} size={680} styles={{ wrapper: { maxWidth: '100vw' } }} closable={!editSaving} mask={{ closable: !editSaving }} onClose={() => { if (!editSaving) setSelected(undefined); }}
      footer={<div style={{ display: 'flex', justifyContent: 'flex-end' }}><Space><Button aria-label={editing ? '取消' : '关闭'} disabled={editSaving} onClick={() => setSelected(undefined)}>{editing ? '取消' : '关闭'}</Button>{editing && <Button type="primary" loading={editSaving} onClick={() => editForm.submit()}>保存修改</Button>}</Space></div>}>
      {selected && <><div className="repository-detail-title"><Tag>{formats.find(item => item.value === selected.format)?.label}</Tag><strong>{selected.name}</strong><span>{selected.version}</span></div>
        {editing ? <Form form={editForm} name="repository-edit" layout="vertical" disabled={editSaving} onFinish={values => void saveEdit(values)}>
          <Form.Item name="name" label="模型名称" rules={[{ required:true,whitespace:true,max:100,message:'请输入模型名称（最多100字）' }]}><Input maxLength={100} /></Form.Item>
          <Form.Item name="group_name" label="模型组（可选）" rules={[{max:100,message:'模型组最多100字'}]} extra="修改分组不改变组件名称、文件或下发引用；留空按名称自动分组。"><Input maxLength={100} placeholder="例如 sam3" /></Form.Item>
          <Form.Item name="version" label="版本号" rules={[{ required:true,whitespace:true,max:100,message:'请输入版本号（最多100字）' }]}><Input maxLength={100} /></Form.Item>
          {buildEditable&&<>
            <Form.Item name="gpu_model" label="GPU 型号" rules={[{required:true,whitespace:true,max:200,message:'请输入实际构建GPU型号（最多200字）'}]}><Input maxLength={200} placeholder="例如 Tesla T4" /></Form.Item>
            <Form.Item name="tensorrt_version" label="TensorRT 版本（可选）" rules={[{max:100,message:'TensorRT版本最多100字'}]} extra="填写构建此engine的实际版本，例如10.14.1；留空后需补全才能下发。"><Input maxLength={100} placeholder="例如 10.14.1" /></Form.Item>
            <Typography.Paragraph type="secondary">仅修正手工录入的构建信息，不改变engine实际兼容性。已有下发记录保留原环境快照；环境信息修正后，未执行的下发记录需重新创建。</Typography.Paragraph>
          </>}
          <Form.Item name="description" label="备注" rules={[{ max:2000,message:'备注最多2000字' }]}><Input.TextArea rows={4} maxLength={2000} showCount placeholder="记录模型用途、适用场景或版本说明" /></Form.Item>
          {editError && <Alert type="error" showIcon title={editError} style={{marginBottom:16}} />}
          <p className="repository-detail-help">修改名称、版本号和备注不会改变模型文件。已有下发记录保留当时的版本信息。</p>
        </Form> : <p className="repository-detail-note">{selected.description || '暂无版本备注'}</p>}
        <h3 className="repository-detail-section">文件与来源</h3><Descriptions column={1} size="small" items={[
        { key: 'id', label: '版本ID', children: selected.id }, { key: 'entry', label: '主文件', children: selected.entry_file },
        { key: 'source', label: '来源', children: selected.run_id ? `训练任务 ${selected.run_id}` : selected.source?.kind === 'training' ? '训练产物（来源任务已移除）' : '直接上传' },
        { key: 'size', label: '总大小', children: bytes(selected.file_size) },
        ...(selected.source?.onnx_input?[{key:'onnx-input',label:'输入 Shape（NCHW）',children:`${selected.source.onnx_input.name} · ${selected.source.onnx_input.shape.join(' × ')}`}]:[]),
        ...(selected.format==='tensorrt'?[{key:'batch',label:'Batch 规格',children:repositoryBatchLabel(selected.batch_profile)}]:[]),
      ]} /><Table rowKey="path" size="small" dataSource={selected.files} pagination={false} scroll={{ x: 600 }} columns={[
        { title: '文件路径', dataIndex: 'path', width:160, render: value => <span style={{ overflowWrap: 'anywhere' }}>{value}</span> },
        { title: '大小', dataIndex: 'bytes', width:100, render: bytes },
        { title: 'SHA256', dataIndex: 'sha256', width:340, render: value => <Typography.Text copyable style={{ fontFamily: 'monospace', fontSize:12, wordBreak:'break-all' }}>{value}</Typography.Text> },
      ]} />{selected.format === 'tensorrt' && <Alert style={{ marginTop: 16 }} type="info" showIcon title="TensorRT 兼容性" description={`构建 GPU：${selected.gpu_model}；TensorRT：${selected.tensorrt_version || '未记录'}。${buildEditable?'直接上传的构建信息可在版本配置中修正；请填写实际构建环境。':'构建信息保持来源记录，此记录不支持手工修改。'}模型文件和SHA256不变，下载后请核对目标环境。`} />}</>}
    </Drawer>
    <Modal open={!!configModel} title="config.pbtxt" width={900} onCancel={()=>setConfigModel(undefined)} footer={<Button aria-label="关闭配置" onClick={()=>setConfigModel(undefined)}>关闭</Button>}>
      <Typography.Paragraph type="secondary">{configModel?.name} · {configModel?.version}</Typography.Paragraph>
      {configLoading ? <Progress percent={100} status="active" showInfo={false} /> : configError ? <Alert type="error" showIcon title={configError} /> : <pre className="repository-config">{configContent}</pre>}
    </Modal>
  </Space>;
}
