import { useEffect, useRef, useState } from 'react';
import JsonInput from '../JsonInput';
import { Alert, Button, Form, Input, InputNumber, Modal, Select, Space, Typography } from 'antd';
import type { Project } from '../../api/projects';
import CheckpointFields from './CheckpointFields';
import type { CheckpointInput } from '../../api/validation';
import { createValidationRun, createValidationSet, getValidationSnapshots, validationError } from '../../api/validation';
import type { ValidationConfig, ValidationSet, ValidationSnapshot } from '../../api/validation';

export function CreateValidationSet({ projects, projectId:initialProjectId, onClose, onCreated }: { projects: Project[]; projectId?:string; onClose: () => void; onCreated: () => void }) {
  const [form] = Form.useForm();
  const projectId = Form.useWatch('project_id', form), source = Form.useWatch('source', form), split = Form.useWatch('split', form) || 'val';
  const [snapshots, setSnapshots] = useState<ValidationSnapshot[]>([]), [loading, setLoading] = useState(false);
  const [snapshotError, setSnapshotError] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false), [reload, setReload] = useState(0);
  const saving = useRef(false), mounted = useRef(false);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now(); setElapsed(0);
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    form.setFieldValue('snapshot_id', undefined); setSnapshots([]); setSnapshotError('');
    if (!projectId || source !== 'snapshot') return;
    const controller = new AbortController();
    setLoading(true);
    getValidationSnapshots(projectId, controller.signal).then(data => {
      if (!controller.signal.aborted) setSnapshots(data);
    }).catch(e => { if (!controller.signal.aborted) setSnapshotError(validationError(e, '冻结包读取失败')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, source, reload, form]);
  const save = async (values: { name: string; project_id: string; source: string; snapshot_id?: string; split: 'val' | 'test' }) => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError('');
    try {
      await createValidationSet({ name: values.name.trim(), project_id: values.project_id, split: values.split || 'val',
        ...(values.source === 'snapshot' ? { snapshot_id: values.snapshot_id } : {}) });
      if (mounted.current) onCreated();
    } catch (e) { if (mounted.current) setError(validationError(e, '验证集创建失败')); }
    finally { saving.current = false; if (mounted.current) setBusy(false); }
  };
  return <Modal open title="创建验证集" onCancel={() => { if (!saving.current) onClose(); }} closable={!busy} mask={{ closable: !busy }} footer={null}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Alert type="info" title="需有已确认的标注真值" description="人工审核通过，或管理员在YOLO导入时选择信任标注的样本可用。AI自动通过不直接纳入；冻结val/test须保留对应版本的确认记录。请使用独立验证项目或划分。" />
      {error && <Alert type="error" title={error} />}
      {busy && <Alert type="info" title={`正在保存验证集，已等待 ${elapsed} 秒`} description="正在核对冻结版本和已确认标注，请勿重复提交。" />}
      <Form form={form} layout="vertical" initialValues={{ source: 'approved', split: 'val', project_id:initialProjectId }} onFinish={save} disabled={busy}>
        <Form.Item name="name" label="验证集名称" rules={[{ required: true, whitespace: true, message: '请输入验证集名称' }]}><Input maxLength={200} /></Form.Item>
        <Form.Item name="project_id" label="验证项目" rules={[{ required: true, message: '请选择检测项目' }]}>
          <Select aria-label="验证项目" showSearch optionFilterProp="label" options={projects.map(p => ({ value: p.id, label: p.project_name }))} />
        </Form.Item>
        <Form.Item name="source" label="真值来源"><Select aria-label="真值来源" options={[{ value: 'approved', label: '当前已确认标注的样本' }, { value: 'snapshot', label: '冻结包 val/test 划分' }]} /></Form.Item>
        {source !== 'snapshot' ? <Alert type="warning" title="当前样本未验证独立于训练数据" description="创建时固定真值，不应仅凭创建时间宣称验证集独立。" /> : <>
          <Alert type="warning" title="冻结时间早不等于独立验证" description="只使用冻结的 val/test 划分；仍需核对训练数据来源和划分证据。旧版未划分包不可选。" />
          <Form.Item name="split" label="冻结划分"><Select aria-label="冻结划分" options={[{ value: 'val', label: 'val（验证）' }, { value: 'test', label: 'test（测试）' }]} onChange={() => form.setFieldValue('snapshot_id', undefined)} /></Form.Item>
          {snapshotError && <Alert type="error" title={snapshotError} action={<Button onClick={() => setReload(n => n + 1)}>重试读取冻结包</Button>} />}
          <Form.Item name="snapshot_id" label="冻结包" rules={[{ required: true, message: '请选择包含对应划分的冻结包' }]}>
            <Select aria-label="冻结包" loading={loading} disabled={!projectId || loading || !!snapshotError} notFoundContent="暂无可用冻结包，请先冻结 val/test 划分"
              options={snapshots.map(s => ({ value: s.id, label: `${s.id} · ${s.format} · ${s.created_at}`,
                disabled: !s.manifest.assets.some(a => a.split === split) }))} />
          </Form.Item>
        </>}
        <Space wrap style={{ marginTop: 16 }}><Button disabled={busy} onClick={onClose}>取消</Button><Button aria-label="保存验证集" type="primary" htmlType="submit" loading={busy}>保存验证集</Button></Space>
      </Form>
    </Space>
  </Modal>;
}

const defaults: ValidationConfig = { inference_confidence: .001, confidence: .25, match_iou: .5, nms_iou: .45, label_mapping: {}, labels: [] };
export function CreateValidationRun({ dataset, onClose, onCreated }: { dataset: ValidationSet; onClose: () => void; onCreated: () => void }) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const saving = useRef(false), mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const save = async (values: ValidationConfig & { target_overlap_percent:number; checkpoint: CheckpointInput; weight_source: string; name?: string; mapping: string }) => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError('');
    try {
      await createValidationRun({ set_id: dataset.id, checkpoint: { ...values.checkpoint, ...(values.weight_source === 'remote' ? { training_run_id: undefined } : {}) }, ...(values.name?.trim() ? { name: values.name.trim() } : {}),
        config: { inference_confidence: values.inference_confidence, confidence: values.confidence, match_iou: values.match_iou, nms_iou: values.nms_iou, target_overlap:values.target_overlap_percent/100,
          label_mapping: JSON.parse(values.mapping), labels: values.labels } });
      if (mounted.current) onCreated();
    } catch (e) { if (mounted.current) setError(validationError(e, '验证任务创建失败')); }
    finally { saving.current = false; if (mounted.current) setBusy(false); }
  };
  return <Modal open title="验证训练权重" width={720} styles={{ body: { maxHeight: '75dvh', overflowY: 'auto' } }} onCancel={() => { if (!saving.current) onClose(); }} closable={!busy} mask={{ closable: !busy }} footer={null}>
    <Typography.Paragraph>验证集：{dataset.name} · {dataset.total} 张 · 真值类别：{dataset.labels.join('、') || '无类别'}</Typography.Paragraph>
    <Typography.Paragraph type="secondary">参数、权重哈希和执行镜像在创建时固定。类别映射方向为训练类别 → 验证集真值类别。</Typography.Paragraph>
    {error && <Alert type="error" title={error} />}
    <Form form={form} layout="vertical" initialValues={{ ...defaults, target_overlap_percent:20, mapping: '{}', weight_source: 'training', checkpoint: { server_id: 'default', kind: 'yolo26', device: 'cpu', image: 'aisfh5.com:9091/ai/jashan_train:yolo-rfdetr', class_names: [], accept_platform_model_license: false } }} onFinish={save} disabled={busy}>
      <CheckpointFields form={form} busy={busy} />
      <Form.Item name="name" label="任务名称（选填）"><Input maxLength={200} /></Form.Item>
      <Form.Item name="inference_confidence" label="推理置信度（AP 使用）" rules={[{ required: true }]}><InputNumber min={0} max={1} step={.001} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="confidence" label="工作点置信度（P/R/F1）" dependencies={['inference_confidence']} rules={[{ required: true }, ({ getFieldValue }) => ({ validator: (_, value) => value >= getFieldValue('inference_confidence') ? Promise.resolve() : Promise.reject(Error('工作点不能低于推理置信度')) })]}><InputNumber min={0} max={1} step={.01} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="target_overlap_percent" label="交集阈值（%）" extra="目标检出判定：交集面积 ÷ 较小框面积；同类别达到此比例算检出，完全包含也算。随任务保存，报告默认使用此设置。" rules={[{required:true}]}><InputNumber aria-label="任务交集阈值（%）" min={1} max={100} step={5} precision={0} style={{width:'100%'}}/></Form.Item>
      <Form.Item name="match_iou" label="标准评测匹配 IoU" rules={[{ required: true }]}><InputNumber min={0} max={1} step={.05} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="nms_iou" label="NMS IoU" rules={[{ required: true }]}><InputNumber min={0} max={1} step={.05} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="labels" label="推理类别（留空使用模型默认）"><Select aria-label="推理类别" mode="tags" options={dataset.labels.map(label => ({ value: label, label }))} /></Form.Item>
      <Form.Item name="mapping" label="类别映射 JSON" rules={[{ validator: (_, text) => {
        try { const value = JSON.parse(text); if (value && !Array.isArray(value) && typeof value === 'object' && Object.entries(value).every(([key, label]) => key.trim() && typeof label === 'string' && label.trim())) return Promise.resolve(); } catch { /* Report invalid JSON beside the field. */ }
        return Promise.reject(Error('请输入字符串到字符串的 JSON 对象'));
      } }]}><JsonInput rows={3} placeholder={'{"person":"人"}'} /></Form.Item>
      <Space wrap><Button onClick={onClose}>取消</Button><Button aria-label="开始验证" type="primary" htmlType="submit" loading={busy}>开始验证</Button></Space>
    </Form>
  </Modal>;
}
