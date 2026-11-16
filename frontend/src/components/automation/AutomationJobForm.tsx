import { useEffect, useRef, useState } from 'react';
import JsonInput from '../JsonInput';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Modal, Select, Space } from 'antd';
import { isAxiosError } from 'axios';
import { createAutomationJob } from '../../api/automation';
import type { AutomationKind } from '../../api/automation';
import type { Project } from '../../api/projects';
import {DEFAULT_DETECTION_CONFIDENCE,getPromptDefaults,isModelOffline,type ModelService} from '../../api/modelServices';
import PreannotationExamplePicker from '../PreannotationExamplePicker';
import type {PreannotationExample} from '../../api/objectAttributes';
import PromptEditor from '../PromptEditor';

type Values = {
  project_id: string; kind: AutomationKind; asset_ids?: string; detector_id?: string; reviewer_id?: string;
  follow_uploads?: boolean;
  confidence: number; iou: number; auto_approve_confidence: number; crop_percent: number;
  auto_review: boolean; label_mapping: string; labels: string[]; prompt: string; review_prompt: string; preannotation_prompt: string; detection_prompt: string;
};
function parseMapping(text: string): Record<string, string> {
  const mapping = JSON.parse(text);
  if (!mapping || Array.isArray(mapping) || typeof mapping !== 'object' || Object.keys(mapping).length > 100 ||
    Object.entries(mapping).some(([key, value]) => !key.trim() || key.length > 100 || typeof value !== 'string' || !value.trim() || value.length > 100)) throw Error('invalid mapping');
  return mapping;
}

export default function AutomationJobForm({ projects, models, projectId, fixedProject=false, onClose, onCreated }: {
  projects: Project[]; models: ModelService[]; projectId?: string; fixedProject?:boolean; onClose: () => void; onCreated: () => void;
}) {
  const [form] = Form.useForm<Values>();
  const kind = Form.useWatch('kind', form) || 'preannotate';
  const selectedProjectId = Form.useWatch('project_id', form);
  const autoReview = Form.useWatch('auto_review', form);
  const detectorId = Form.useWatch('detector_id', form);
  const reviewerId = Form.useWatch('reviewer_id', form);
  const reviewCore = Form.useWatch('review_prompt', form);
  const exampleCore = Form.useWatch('detection_prompt', form);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [exampleMode,setExampleMode]=useState(false),[example,setExample]=useState<PreannotationExample>(),[preannotationModel,setPreannotationModel]=useState<string>();
  const [detectionPromptDefault,setDetectionPromptDefault]=useState<string>();
  useEffect(()=>{let active=true;getPromptDefaults().then(defaults=>{if(active){setDetectionPromptDefault(defaults.detection_preannotation);if(!form.isFieldTouched('detection_prompt'))form.setFieldValue('detection_prompt',defaults.detection_preannotation);}}).catch(()=>{});return()=>{active=false;};},[form]);
  const detectionExample=kind==='preannotate'&&exampleMode;
  const saving = useRef(false);
  const detectors = models.filter(model => model.enabled && model.kind === 'detection');
  const reviewers = models.filter(model => model.enabled && model.kind === 'caption' && model.protocol === 'openai_chat');
  const reviewerPrompt = (id?:string) => reviewers.find(model=>model.id===id)?.chat_prompts?.review||'';
  const options = (items: ModelService[]) => items.map(model => ({ value: model.id, disabled:isModelOffline(model), label: `${model.name} · ${model.model_name || model.key}${isModelOffline(model)?' · 离线':''}` }));
  const projectLabels = (id?: string) => {
    const labels = projects.find(project => project.id === id)?.label_schema?.labels;
    return Array.isArray(labels) ? labels.filter((label): label is string => typeof label === 'string') : [];
  };
  const submit = async () => {
    if (saving.current) return;
    let values: Values;
    try { values = await form.validateFields(); } catch { return; }
    if(kind==='preannotate'&&exampleMode&&(!example||!preannotationModel)){setError('请选择视觉大模型和一张人工正确范例');return;}
    saving.current = true; setBusy(true); setError('');
    try {
      const { project_id, kind, detector_id, reviewer_id, confidence, iou, auto_approve_confidence, auto_review, labels, prompt } = values;
      const reviewing = kind === 'review' || auto_review&&!exampleMode;
      const assetIds = [...new Set((values.asset_ids || '').split(/[\s,，]+/).filter(Boolean))];
      await createAutomationJob({ project_id: fixedProject ? projectId! : project_id, kind, ...(assetIds.length ? { asset_ids: assetIds } : {}),
        follow_uploads:!!values.follow_uploads,
        ...(kind === 'preannotate' ? exampleMode?{preannotation_model_id:preannotationModel,example_asset_id:example?.asset_id,example_annotation_version:example?.annotation_version}:{ detector_id } : {}), ...((kind === 'review' || auto_review&&!exampleMode) ? { reviewer_id } : {}),
        config: { confidence:confidence??DEFAULT_DETECTION_CONFIDENCE, iou:iou??.45, auto_approve_confidence:auto_approve_confidence??.9, crop_expansion: (values.crop_percent??20) / 100,
          auto_review: kind === 'review' || !exampleMode&&!!auto_review, labels: labels || [], prompt: reviewing?prompt||'':'',
          ...(reviewing?{review_prompt:values.review_prompt||''}:{}), ...(detectionExample?{detection_prompt:values.detection_prompt||'',preannotation_prompt:values.preannotation_prompt||''}:{}), label_mapping: detectionExample?{}:parseMapping(values.label_mapping) } });
      onCreated();
    } catch (error) {
      const detail=isAxiosError(error)?error.response?.data?.detail:undefined;
      setError(typeof detail==='string'?detail:'任务创建失败，请检查配置后重试。');
    }
    finally { saving.current = false; setBusy(false); }
  };
  return <Modal open title="创建预标注 / AI审核任务" width={720} styles={{ body: { maxHeight: '65dvh', overflowY: 'auto' } }} onCancel={() => { if (!saving.current) onClose(); }}
    mask={{ closable: !busy }} closable={!busy} footer={<Space><Button disabled={busy} onClick={onClose}>关闭创建</Button><Button type="primary" loading={busy} onClick={submit}>提交任务</Button></Space>}>
    <Alert type="info" showIcon title="预标注只处理未标注图片；已有标注不覆盖。AI审核只处理已有标注，不改人工框。" style={{ marginBottom: 16 }} />
    <Form form={form} layout="vertical" disabled={busy} initialValues={{ project_id: projectId, kind: 'preannotate', detector_id: detectors.find(model=>!isModelOffline(model))?.id,
      reviewer_id: reviewers.find(model=>!isModelOffline(model))?.id, confidence: DEFAULT_DETECTION_CONFIDENCE, iou: .45, auto_approve_confidence: .9, crop_percent: 20,
      auto_review: true, label_mapping: '{}', labels: [], prompt: '', review_prompt:reviewerPrompt(reviewers.find(model=>!isModelOffline(model))?.id), preannotation_prompt:'', detection_prompt:'' }}>
      <Form.Item label="项目" name="project_id" rules={[{ required: true, message: '请选择检测项目' }]}>
        <Select aria-label="任务项目" disabled={fixedProject||busy} showSearch optionFilterProp="label" options={projects.map(p => ({ value: p.id, label: p.project_name }))}
          onChange={() => {form.setFieldValue('labels', []);setExample(undefined);}} />
      </Form.Item>
      <Form.Item label="任务类型" name="kind"><Select aria-label="任务类型" onChange={() => {form.setFieldValue('labels', []);form.setFieldValue('follow_uploads',false);}} options={[{ value: 'preannotate', label: '批量预标注（仅未标注）' }, { value: 'review', label: 'AI审核（已有标注）' }]} /></Form.Item>
      <Form.Item name="follow_uploads" valuePropName="checked"
        extra={kind==='review'?'新图随上传带入有效标注后自动进入审核，可在任务明细关闭。':'新图上传并加入项目后自动入队，可在任务明细关闭。'}>
        <Checkbox>自动加入新上传图片</Checkbox>
      </Form.Item>
      <Form.Item label="资产 ID（可选）" name="asset_ids" extra="留空按整个项目；填写时用逗号、空格或换行分隔。每个任务最多 10000 张图片。"
        rules={[{ validator: async (_, value) => { if (new Set((value || '').split(/[\s,，]+/).filter(Boolean)).size > 10000) throw Error('单任务最多 10000 张图片'); } }]}>
        <Input.TextArea aria-label="资产 ID（可选）" autoSize={{ minRows: 2, maxRows: 5 }} />
      </Form.Item>
      {kind === 'preannotate' && <>
        <Checkbox checked={exampleMode} disabled={busy} onChange={event=>{setExampleMode(event.target.checked);setExample(undefined);}}>大模型按正确范例预标注检测框</Checkbox>
        {exampleMode?<Space orientation="vertical" style={{width:'100%',marginBlock:16}}><Select aria-label="范例预标注大模型" value={preannotationModel} onChange={setPreannotationModel} placeholder="选择视觉大模型" options={options(reviewers)} style={{width:'100%'}}/>
          {selectedProjectId&&<PreannotationExamplePicker key={selectedProjectId} projectId={selectedProjectId} value={example} disabled={busy} onChange={setExample}/>}
          <Alert type="info" title="大模型接收范例图片及人工检测框，生成其他图片的框；结果全部人工待审，不自动通过。"/>
          <Form.Item name="detection_prompt" label="目标检测提示词" style={{marginBottom:0}}>
            <PromptEditor key={preannotationModel} title="范例目标检测核心提示词" buttonLabel="编辑目标检测提示词" ariaLabel="目标检测核心提示词" disabled={busy} defaultValue={detectionPromptDefault}
              help="编辑本次范例预标注的完整检测核心指令；留空使用平台默认核心，不修改模型配置。" readOnlyContext="系统追加项目类别、人工正确范例及检测框 JSON 结构和坐标规则，结果按固定结构校验。"/>
          </Form.Item>
          <Form.Item name="preannotation_prompt" label="本次预标注补充要求（可选）" style={{marginBottom:0}}>
            <PromptEditor key={preannotationModel} title="本次范例预标注补充要求" buttonLabel="编辑检测补充要求" ariaLabel="范例预标注补充要求" disabled={busy} defaultValue="" referencePrompt={exampleCore||detectionPromptDefault} referenceLabel="本次范例预标注核心提示词" help="仅补充本次预标注要求；返回结构和坐标规则保持不变，不修改模型配置。"/>
          </Form.Item>
        </Space>:<><Form.Item label="检测模型" name="detector_id" rules={[{ required: true, message: '请选择已启用的检测模型' }]}><Select aria-label="检测模型" options={options(detectors)} /></Form.Item>
        <Button disabled={!detectorId || busy} onClick={() => {
          const model = detectors.find(item => item.id === detectorId);
          if (model) form.setFieldsValue({ confidence: model.confidence, iou: model.iou, labels: model.labels, label_mapping: JSON.stringify(model.label_mapping, null, 2) });
        }} style={{ marginBottom: 16 }}>复制检测模型默认参数</Button>
        <Form.Item name="auto_review" valuePropName="checked"><Checkbox>预标注后自动调用AI审核</Checkbox></Form.Item></>}
      </>}
      {(kind === 'review' || autoReview&&!exampleMode) && <><Form.Item label="审核模型" name="reviewer_id" rules={[{ required: true, message: '请选择已启用的 OpenAI Chat 视觉审核模型' }]}
        extra="使用已启用的 caption / OpenAI Chat 服务（可选择当前部署的 Qwen3.8）；按协议筛选，不依赖模型名称。">
        <Select aria-label="审核模型" options={options(reviewers)} onChange={id=>form.setFieldValue('review_prompt',reviewerPrompt(id))} />
      </Form.Item>
      <Form.Item name="review_prompt" label="本次审核核心提示词">
        <PromptEditor key={reviewerId} title="本次审核核心提示词" buttonLabel="编辑审核提示词" ariaLabel="审核核心提示词" disabled={busy||!reviewerId} defaultValue={reviewerPrompt(reviewerId)} help="仅用于本任务；留空使用所选模型默认提示词，不修改全局模型配置。"/>
      </Form.Item>
      </>}
      {!detectionExample&&<><Space wrap align="start">
        {([['confidence', '检测置信度'], ['iou', '检测 IoU'], ['auto_approve_confidence', '自动通过置信度']] as const).map(([name, label]) =>
          <Form.Item key={name} name={name} label={label} rules={[{ required: true, type: 'number', min: 0, max: 1, message: '请输入 0–1 的数值' }]}><InputNumber aria-label={label} min={0} max={1} step={.01} /></Form.Item>)}
        <Form.Item name="crop_percent" label="每边外扩百分比" rules={[{ required: true, type: 'number', min: 0, max: 100, message: '请输入 0–100 的百分比' }]}>
          <InputNumber aria-label="每边外扩百分比" min={0} max={100} suffix="%" />
        </Form.Item>
      </Space>
      <p>外扩按框宽高计算，每边默认 20%。所有框置信度均严格高于自动通过阈值，并经审核模型整图完整性检查、外扩框确认通过后，才可自动通过。低分或不确定项送管理员待审。</p></>}
      <Alert type="warning" showIcon title="负样本全部由管理员最终审核；零框不自动作为负样本通过。" style={{ marginBottom: 16 }} />
      <Form.Item name="labels" label={detectionExample?'预标注类别':kind === 'review' ? '审核类别' : '检测类别 / 提示'} extra={detectionExample?'只检测这些项目类别，留空使用全部项目类别。':kind === 'review' ? '仅检查所选类别是否漏标；留空检查全部项目类别。未选类别无需补标，所有已有框仍逐框核验。历史审核结论不会自动更新。' : '留空使用模型默认类别。模型输出的新类别自动加入项目，首批新类别的框进入人工审核。'}
        rules={[{ validator: async (_, labels: string[]) => { if (labels?.some(label => !label.trim() || label.length > 100)) throw Error('每个类别须为 1–100 个字符'); } }]}>
        <Select aria-label={detectionExample?'预标注类别':kind === 'review' ? '审核类别' : '检测类别 / 提示'} mode={kind === 'review'||detectionExample ? 'multiple' : 'tags'} options={kind === 'review'||detectionExample ? projectLabels(selectedProjectId).map(value => ({value,label:value})) : undefined} tokenSeparators={[',', '，']} maxCount={100} allowClear />
      </Form.Item>
      {!detectionExample&&<><Form.Item name="label_mapping" label="类别映射 JSON" rules={[{ validator: async (_, text) => { try { parseMapping(text); } catch { throw Error('请输入字符串到字符串的 JSON 对象'); } } }]}>
        <JsonInput aria-label="类别映射 JSON" autoSize={{ minRows: 2, maxRows: 8 }} />
      </Form.Item>
      {(kind === 'review' || autoReview&&!exampleMode)&&<Form.Item name="prompt" label="补充审核要求"><PromptEditor title="本次审核补充要求" buttonLabel="编辑审核补充要求" ariaLabel="审核提示词" disabled={busy} maxLength={2000} defaultValue="" referencePrompt={reviewCore||reviewerPrompt(reviewerId)} referenceLabel="本次审核核心提示词" help="追加到本次审核核心提示词之后，仅用于本任务。"/></Form.Item>}</>}
      {error && <Alert type="error" showIcon title={error} />}
    </Form>
  </Modal>;
}
