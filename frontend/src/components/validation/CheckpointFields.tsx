import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, Select, Typography } from 'antd';
import type { FormInstance } from 'antd';
import { getTrainingArtifacts, getTrainingRuns, getTrainingServers, type TrainingRun, type TrainingArtifact } from '../../api/training';
import { getGpuResources, type GpuServer, type GpuResource } from '../../api/gpuResources';

export default function CheckpointFields({ form, busy, preview = false, taskType='detection' }: { form: FormInstance; busy: boolean; preview?: boolean; taskType?:'detection'|'caption'|'embedding' }) {
  const multimodal=taskType!=='detection';
  const [runs, setRuns] = useState<TrainingRun[]>([]), [servers, setServers] = useState<GpuServer[]>([]);
  const [artifacts, setArtifacts] = useState<TrainingArtifact[]>([]), [loading, setLoading] = useState(false);
  const [error, setError] = useState(''), [gpus, setGpus] = useState<GpuResource[]>([]);
  const [gpuLoading, setGpuLoading] = useState(false);
  const [artifactRevision, setArtifactRevision] = useState(0);
  const source = Form.useWatch('weight_source', form) || 'training';
  const trainingId = Form.useWatch(['checkpoint', 'training_run_id'], form);
  const kind = Form.useWatch(['checkpoint', 'kind'], form) || 'yolo26';
  const serverId = Form.useWatch(['checkpoint', 'server_id'], form) || 'default';
  const gpuRequest = useRef({ revision: 0 });
  useEffect(() => {
    let active = true;
    const requestState = gpuRequest.current;
    getTrainingServers().then(rows => { if (active) setServers(rows.filter(server => server.enabled)); }).catch(() => { if (active) setError('服务器列表读取失败，请关闭后重试'); });
    (async () => {
      const all: TrainingRun[] = [];
      for (let page = 1; ; page++) {
        const data = await getTrainingRuns(page); if (!active) return;
        all.push(...data.items); if (all.length >= data.total || !data.items.length) break;
      }
      setRuns(all.filter(run => (preview ? run.status !== 'draft' : ['completed', 'failed', 'canceled'].includes(run.status)) && (multimodal?['swift_sft','wemm'].includes(run.kind)&&(taskType==='embedding')===(run.kind==='wemm'||run.config.parameters.task_type==='embedding'):['yolo26', 'rfdetr_medium', 'rfdetr_2xlarge', 'dinov3_vits16_ltdetr'].includes(run.kind))));
    })().catch(() => { if (active) setError('训练任务读取失败，仍可填写服务器已有权重路径'); });
    return () => { active = false; requestState.revision++; };
  }, [preview,multimodal,taskType]);
  useEffect(() => {
    if (source !== 'training' || !trainingId) return;
    let active = true;
    getTrainingArtifacts(trainingId).then(data => { if (active) {
      const files = data.files.filter(file => multimodal?/(^|\/)(adapter_config\.json|config\.json|(?:adapter_model|model|pytorch_model)[^/]*\.(safetensors|bin))$/i.test(file.path):file.role === 'weights' && /\.(pt|pth|ckpt)$/i.test(file.path));
      setArtifacts(files);
      const run = runs.find(item => item.id === trainingId), selected = form.getFieldValue(['checkpoint', 'weight_path']);
      if (selected && !files.some(file => (multimodal?`${run?.output_dir}/${run?.config.output_subdir}/${file.path}`.replace(/\/[^/]+$/,''):`${run?.output_dir}/${run?.config.output_subdir}/${file.path}`) === selected)) form.setFieldValue(['checkpoint', 'weight_path'], undefined);
      if (data.truncated) setError('产物列表已截断，可切换为服务器路径填写完整权重路径');
    } })
      .catch(() => { if (active) setError('训练权重列表读取失败，请检查服务器连接'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [trainingId, source, artifactRevision, runs, form,multimodal]);
  const clearGpu = () => { gpuRequest.current.revision++; setGpus([]); setGpuLoading(false); form.setFieldValue(['checkpoint', 'device'], 'cpu'); };
  const loadGpus = async () => {
    const request = ++gpuRequest.current.revision; setGpuLoading(true); setError('');
    try { const data = await getGpuResources(undefined, serverId); if (request === gpuRequest.current.revision) setGpus(data.gpus); }
    catch { if (request === gpuRequest.current.revision) setError('GPU读取失败，可继续使用CPU验证'); }
    finally { if (request === gpuRequest.current.revision) setGpuLoading(false); }
  };
  const training = runs.find(run => run.id === trainingId);
  const statusLabels: Record<string, string> = { starting: '启动中', running: '运行中', stopping: '停止中', unknown: '状态待核对', completed: '已完成', failed: '失败', canceled: '已取消' };
  const activeTraining = training && ['starting', 'running', 'stopping', 'unknown'].includes(training.status);
  return <>
    <Alert type="info" showIcon title="直接验证训练权重，无需部署服务" description="独立容器加载权重并生成预测；默认CPU，可读取显卡后选择空闲GPU。权重和镜像固定哈希，不启动训练。" style={{ marginBottom: 16 }} />
    {error && <Alert type="warning" showIcon title={error} style={{ marginBottom: 16 }} />}
    <Form.Item name="weight_source" label="权重来源"><Select aria-label="权重来源" options={[{ value: 'training', label: '平台训练任务的权重产物' }, { value: 'remote', label: '服务器已有权重路径' }]} onChange={() => { form.setFieldValue(['checkpoint', 'training_run_id'], undefined); form.setFieldValue(['checkpoint', 'weight_path'], undefined); clearGpu(); }} /></Form.Item>
    {source === 'training' && <>
      <Form.Item name={['checkpoint', 'training_run_id']} label="训练任务" rules={[{ required: true, message: '请选择检测训练任务' }]} extra={preview ? '支持运行中任务已保存的 checkpoint；尚未保存权重时，可稍后刷新权重列表。' : '运行中的训练不在候选中；已结束任务仍须实际存在权重产物。'}>
        <Select aria-label="权重训练任务" showSearch optionFilterProp="label" options={runs.map(run => ({ value: run.id, label: `${run.name} · ${run.kind} · ${statusLabels[run.status] || run.status}` }))} onChange={id => {
          const run = runs.find(item => item.id === id); if (!run) return;
          setLoading(true); setArtifacts([]); setError('');
          let names = run.config.selected_labels || run.source.labels || [];
          if (!names.length) { try { const metadata = JSON.parse(run.inspection.checks?.find(check => check.name === '训练数据与类别')?.detail || '{}'); if (Array.isArray(metadata.labels)) names = metadata.labels; } catch { /* Keep manual class order when metadata is unavailable. */ } }
          clearGpu(); form.setFieldsValue({ checkpoint: { server_id: run.server.id, kind: multimodal?taskType:run.kind, image: run.config.image, weight_path: undefined,
            ...(multimodal?{base_model_path:run.config.parameters.tuner_type==='full'?'':run.config.model_path,model_type:''}:{}),
            class_names: run.kind === 'dinov3_vits16_ltdetr' ? [] : names, accept_platform_model_license: run.config.accept_platform_model_license } });
        }} />
      </Form.Item>
      {preview && activeTraining && <Alert type="info" showIcon title="训练仍在进行，预览仅加载已保存的权重" description="默认使用CPU，不占用训练GPU。若读取期间权重被更新，校验会中止预览，请刷新权重后重新开始。" style={{ marginBottom: 16 }} />}
      <Form.Item name={['checkpoint', 'weight_path']} label={multimodal?'模型 / LoRA目录':'权重文件'} rules={[{ required: true, message: '请选择训练权重' }]}>
        <Select aria-label="训练权重文件" loading={loading} disabled={!training || loading} notFoundContent="未找到权重，可选择其他任务或填写服务器权重路径" options={multimodal?Array.from(new Set(artifacts.map(file=>`${training?.output_dir}/${training?.config.output_subdir}/${file.path}`.replace(/\/[^/]+$/,'')))).map(path=>({value:path,label:path})):artifacts.map(file => ({ value: `${training?.output_dir}/${training?.config.output_subdir}/${file.path}`, label: `${file.path} · ${(file.bytes / 1024 ** 2).toFixed(1)} MiB` }))} />
      </Form.Item>
      {training && <div style={{ marginTop: -12, marginBottom: 16 }}>
        <Button size="small" onClick={() => { setLoading(true); setError(''); setArtifactRevision(value => value + 1); }} loading={loading} disabled={busy}>刷新权重列表</Button>
        {!loading && !error && !artifacts.length && <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>此任务输出目录中尚未找到检测权重。请等待 checkpoint 保存、选择其他任务，或<Button type="link" size="small" onClick={() => { form.setFieldValue('weight_source', 'remote'); form.setFieldValue(['checkpoint', 'training_run_id'], undefined); form.setFieldValue(['checkpoint', 'weight_path'], undefined); }}>填写服务器权重路径</Button>。</Typography.Paragraph>}
      </div>}
    </>}
    <Form.Item name={['checkpoint', 'server_id']} label="验证服务器" rules={[{ required: true }]}><Select aria-label="验证服务器" disabled={source === 'training' || busy} options={servers.map(server => ({ value: server.id, label: server.name }))} onChange={clearGpu} /></Form.Item>
    <Form.Item name={['checkpoint', 'kind']} label="模型类型" hidden={multimodal} rules={[{ required: true }]}><Select aria-label="权重模型类型" disabled={source === 'training' || busy} onChange={value => form.setFieldsValue({ checkpoint: { image: value === 'dinov3_vits16_ltdetr' ? 'lightly/train:latest' : 'aisfh5.com:9091/ai/jashan_train:yolo-rfdetr', class_names: [] } })} options={[{ value: 'yolo26', label: 'YOLO' }, { value: 'rfdetr_medium', label: 'RF-DETR Medium' }, { value: 'rfdetr_2xlarge', label: 'RF-DETR 2XLarge' }, { value: 'dinov3_vits16_ltdetr', label: 'DINOv3 ViT-S/16 · LTDETR' },...(multimodal?[{value:taskType,label:taskType==='caption'?'图片描述':'向量检索'}]:[])]} /></Form.Item>
    {source === 'remote' && <>
      <Form.Item name={['checkpoint', 'weight_path']} label={multimodal?'模型 / LoRA目录绝对路径':'服务器权重绝对路径'} rules={[{ required: true, message: '请输入权重路径' }]}><Input aria-label="服务器权重路径" placeholder={multimodal?'/ai/chen/models/merged 或 checkpoint-1000':'/ai/chen/training/.../best.pt 或 checkpoint_best_total.pth'} maxLength={500} /></Form.Item>
      <Form.Item name={['checkpoint', 'image']} label="验证镜像" rules={[{ required: true }]} extra="使用服务器已有检测训练镜像，不会自动下载镜像或权重。"><Input aria-label="权重验证镜像" maxLength={500} /></Form.Item>
    </>}
    {multimodal?<>
      <Form.Item name={['checkpoint','base_model_path']} label="LoRA基础模型目录（合并模型留空）" extra="模型目录是LoRA checkpoint时必填；基础模型与LoRA均按内容固定版本。"><Input aria-label="LoRA基础模型目录" maxLength={500}/></Form.Item>
      <Form.Item name={['checkpoint','model_type']} label="Swift model_type（需要时填写）"><Input aria-label="Swift模型类型" placeholder="例如 wemm_embedding" maxLength={100}/></Form.Item>
    </>:<Form.Item name={['checkpoint', 'class_names']} label="训练类别顺序" rules={[{ required: kind.startsWith('rfdetr'), message: '请按训练时的类别编号顺序填写' }]} extra="RF-DETR必填，按类别编号从0开始依次输入；不要用验证集类别顺序代替训练顺序。YOLO和DINOv3留空从完整检测权重读取。"><Select aria-label="训练类别顺序" mode="tags" tokenSeparators={[',', '，']} /></Form.Item>}
    {kind === 'rfdetr_2xlarge' && <Form.Item name={['checkpoint', 'accept_platform_model_license']} valuePropName="checked"><Checkbox>确认 RF-DETR 2XLarge Platform Model License</Checkbox></Form.Item>}
    <Form.Item name={['checkpoint', 'device']} label="验证设备" rules={[{ required: true }]}><Select aria-label="权重验证设备" options={[{ value: 'cpu', label: 'CPU（默认，不占用GPU）' }, ...gpus.map(gpu => ({ value: gpu.uuid, label: `GPU ${gpu.index} · ${gpu.name}` }))]} /></Form.Item>
    <Button onClick={loadGpus} loading={gpuLoading} disabled={busy}>读取显卡</Button>
    <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>选择GPU后，任务创建会预留该卡；执行前再次检查实际占用。停止确认前占用保留，不能抢占训练。{multimodal?'容器限制4核和96GiB内存，大模型建议选择显存足够的空闲GPU。':'CPU任务限制4核和24GiB内存。'}</Typography.Paragraph>
  </>;
}
