import { useRef, useState } from 'react';
import JsonInput from '../JsonInput';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Modal, Select, Space } from 'antd';
import { createAutomationJob } from '../../api/automation';
import type { AutomationKind } from '../../api/automation';
import type { Project } from '../../api/projects';
import type { ModelService } from '../../api/modelServices';

type Values = {
  project_id: string; kind: AutomationKind; asset_ids?: string; detector_id?: string; reviewer_id?: string;
  confidence: number; iou: number; auto_approve_confidence: number; crop_percent: number;
  auto_review: boolean; label_mapping: string; labels: string[]; prompt: string;
};
function parseMapping(text: string): Record<string, string> {
  const mapping = JSON.parse(text);
  if (!mapping || Array.isArray(mapping) || typeof mapping !== 'object' || Object.keys(mapping).length > 100 ||
    Object.entries(mapping).some(([key, value]) => !key.trim() || key.length > 100 || typeof value !== 'string' || !value.trim() || value.length > 100)) throw Error('invalid mapping');
  return mapping;
}

export default function AutomationJobForm({ projects, models, projectId, onClose, onCreated }: {
  projects: Project[]; models: ModelService[]; projectId?: string; onClose: () => void; onCreated: () => void;
}) {
  const [form] = Form.useForm<Values>();
  const kind = Form.useWatch('kind', form) || 'preannotate';
  const selectedProjectId = Form.useWatch('project_id', form);
  const autoReview = Form.useWatch('auto_review', form);
  const detectorId = Form.useWatch('detector_id', form);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const saving = useRef(false);
  const detectors = models.filter(model => model.enabled && model.kind === 'detection');
  const reviewers = models.filter(model => model.enabled && model.kind === 'caption' && model.protocol === 'openai_chat');
  const options = (items: ModelService[]) => items.map(model => ({ value: model.id, label: `${model.name} · ${model.model_name || model.key}` }));
  const projectLabels = (id?: string) => {
    const labels = projects.find(project => project.id === id)?.label_schema?.labels;
    return Array.isArray(labels) ? labels.filter((label): label is string => typeof label === 'string') : [];
  };
  const submit = async () => {
    if (saving.current) return;
    let values: Values;
    try { values = await form.validateFields(); } catch { return; }
    saving.current = true; setBusy(true); setError('');
    try {
      const { project_id, kind, detector_id, reviewer_id, confidence, iou, auto_approve_confidence, auto_review, labels, prompt } = values;
      const assetIds = [...new Set((values.asset_ids || '').split(/[\s,，]+/).filter(Boolean))];
      await createAutomationJob({ project_id, kind, ...(assetIds.length ? { asset_ids: assetIds } : {}),
        ...(kind === 'preannotate' ? { detector_id } : {}), ...((kind === 'review' || auto_review) ? { reviewer_id } : {}),
        config: { confidence, iou, auto_approve_confidence, crop_expansion: values.crop_percent / 100,
          auto_review: kind === 'review' || auto_review, labels: labels || [], prompt: prompt || '', label_mapping: parseMapping(values.label_mapping) } });
      onCreated();
    } catch { setError('任务创建失败，请检查配置后重试。'); }
    finally { saving.current = false; setBusy(false); }
  };
  return <Modal open title="创建预标注 / AI审核任务" width={720} styles={{ body: { maxHeight: '65dvh', overflowY: 'auto' } }} onCancel={() => { if (!saving.current) onClose(); }}
    mask={{ closable: !busy }} closable={!busy} footer={<Space><Button disabled={busy} onClick={onClose}>关闭创建</Button><Button type="primary" loading={busy} onClick={submit}>提交任务</Button></Space>}>
    <Alert type="info" showIcon title="预标注只处理未标注图片；已有标注不覆盖。AI审核只处理已有标注，不改人工框。" style={{ marginBottom: 16 }} />
    <Form form={form} layout="vertical" disabled={busy} initialValues={{ project_id: projectId, kind: 'preannotate', detector_id: detectors[0]?.id,
      reviewer_id: reviewers[0]?.id, confidence: .25, iou: .45, auto_approve_confidence: .9, crop_percent: 20,
      auto_review: true, label_mapping: '{}', labels: projectLabels(projectId), prompt: '' }}>
      <Form.Item label="项目" name="project_id" rules={[{ required: true, message: '请选择检测项目' }]}>
        <Select aria-label="任务项目" showSearch optionFilterProp="label" options={projects.map(p => ({ value: p.id, label: p.project_name }))}
          onChange={id => form.setFieldValue('labels', projectLabels(id))} />
      </Form.Item>
      <Form.Item label="任务类型" name="kind"><Select aria-label="任务类型" onChange={() => form.setFieldValue('labels', projectLabels(form.getFieldValue('project_id')))} options={[{ value: 'preannotate', label: '批量预标注（仅未标注）' }, { value: 'review', label: 'AI审核（已有标注）' }]} /></Form.Item>
      <Form.Item label="资产 ID（可选）" name="asset_ids" extra="留空按整个项目；填写时用逗号、空格或换行分隔。每个任务最多 10000 张图片。"
        rules={[{ validator: async (_, value) => { if (new Set((value || '').split(/[\s,，]+/).filter(Boolean)).size > 10000) throw Error('单任务最多 10000 张图片'); } }]}>
        <Input.TextArea aria-label="资产 ID（可选）" autoSize={{ minRows: 2, maxRows: 5 }} />
      </Form.Item>
      {kind === 'preannotate' && <>
        <Form.Item label="检测模型" name="detector_id" rules={[{ required: true, message: '请选择已启用的检测模型' }]}><Select aria-label="检测模型" options={options(detectors)} /></Form.Item>
        <Button disabled={!detectorId || busy} onClick={() => {
          const model = detectors.find(item => item.id === detectorId);
          if (model) form.setFieldsValue({ confidence: model.confidence, iou: model.iou, labels: model.labels, label_mapping: JSON.stringify(model.label_mapping, null, 2) });
        }} style={{ marginBottom: 16 }}>复制检测模型默认参数</Button>
        <Form.Item name="auto_review" valuePropName="checked"><Checkbox>预标注后自动调用AI审核</Checkbox></Form.Item>
      </>}
      {(kind === 'review' || autoReview) && <Form.Item label="审核模型" name="reviewer_id" rules={[{ required: true, message: '请选择已启用的 OpenAI Chat 视觉审核模型' }]}
        extra="使用已启用的 caption / OpenAI Chat 服务（可选择当前部署的 Qwen3.8）；按协议筛选，不依赖模型名称。">
        <Select aria-label="审核模型" options={options(reviewers)} />
      </Form.Item>}
      <Space wrap align="start">
        {([['confidence', '检测置信度'], ['iou', '检测 IoU'], ['auto_approve_confidence', '自动通过置信度']] as const).map(([name, label]) =>
          <Form.Item key={name} name={name} label={label} rules={[{ required: true, type: 'number', min: 0, max: 1, message: '请输入 0–1 的数值' }]}><InputNumber aria-label={label} min={0} max={1} step={.01} /></Form.Item>)}
        <Form.Item name="crop_percent" label="每边外扩百分比" rules={[{ required: true, type: 'number', min: 0, max: 100, message: '请输入 0–100 的百分比' }]}>
          <InputNumber aria-label="每边外扩百分比" min={0} max={100} suffix="%" />
        </Form.Item>
      </Space>
      <p>外扩按框宽高计算，每边默认 20%。所有框置信度均严格高于自动通过阈值，并经审核模型整图完整性检查、外扩框确认通过后，才可自动通过。低分或不确定项送管理员待审。</p>
      <Alert type="warning" showIcon title="负样本全部由管理员最终审核；零框不自动作为负样本通过。" style={{ marginBottom: 16 }} />
      <Form.Item name="labels" label={kind === 'review' ? '审核类别' : '检测类别 / 提示'} extra={kind === 'review' ? '仅检查所选类别是否漏标；留空检查全部项目类别。未选类别无需补标，所有已有框仍逐框核验。历史审核结论不会自动更新。' : '每项输入后回车；类别映射目标需对应项目标注类别。'}
        rules={[{ validator: async (_, labels: string[]) => { if (labels?.some(label => !label.trim() || label.length > 100)) throw Error('每个类别须为 1–100 个字符'); } }]}>
        <Select aria-label={kind === 'review' ? '审核类别' : '检测类别 / 提示'} mode={kind === 'review' ? 'multiple' : 'tags'} options={kind === 'review' ? projectLabels(selectedProjectId).map(value => ({value,label:value})) : undefined} tokenSeparators={[',', '，']} maxCount={100} allowClear />
      </Form.Item>
      <Form.Item name="label_mapping" label="类别映射 JSON" rules={[{ validator: async (_, text) => { try { parseMapping(text); } catch { throw Error('请输入字符串到字符串的 JSON 对象'); } } }]}>
        <JsonInput aria-label="类别映射 JSON" autoSize={{ minRows: 2, maxRows: 8 }} />
      </Form.Item>
      <Form.Item name="prompt" label="补充审核要求" extra="追加到所选模型的审核核心提示词之后，仅影响本任务；完整核心指令可在模型管理中编辑。"><Input.TextArea aria-label="审核提示词" maxLength={2000} autoSize={{ minRows: 2, maxRows: 8 }} /></Form.Item>
      {error && <Alert type="error" showIcon title={error} />}
    </Form>
  </Modal>;
}
