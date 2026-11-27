import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import CursorPager from './CursorPager';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ExperimentOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import { getModelServices, getPromptDefaults, isModelOffline, type ModelService } from '../api/modelServices';
import type { Project } from '../api/projects';
import { attributeTargets, type AttributeSchema } from '../api/objectAttributes';
import { operations } from './WorkflowStepsFields';
import AttributeProbabilities from './AttributeProbabilities';
import WorkflowGraphBuilder from './workflow/WorkflowGraphBuilder';
import WorkflowCanvas from './workflow/WorkflowCanvas';
import WorkflowDryRunModal from './workflow/WorkflowDryRunModal';
import {
  MODEL_OPERATIONS,
  createDefaultStep,
  normalizeGraph,
} from './workflow/workflowGraph';
import {
  cancelWorkflow,
  createWorkflow,
  deleteWorkflow,
  followWorkUploads,
  getWork,
  getWorkflowHistoryConfig,
  getWorkflows,
  getWorkJobs,
  getWorkflowTemplates,
  saveWorkflowTemplate,
  workAction,
  type WorkDetail,
  type WorkJob,
  type WorkflowDryRunNodeReport,
  type WorkflowEdge,
  type WorkflowHistoryConfig,
  type WorkflowOperation,
  type WorkflowRun,
  type WorkflowStep,
  type WorkflowTemplate,
} from '../api/projectWork';

const errorText = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail
    : '操作失败，请重试';
const running = (job: WorkJob) => !!(job.counts.pending || job.counts.running || job.retrying_count);
const needsManualRetry = (job: WorkJob) => (job.counts.failed || 0) > (job.retrying_count || 0) || !!job.counts.cancelled;
const waitingToStart = (job: WorkJob) => job.workflow_started === false && !job.workflow_cancelled;
const completedResults = (job: WorkJob) => (job.counts.completed || 0) - (job.attribute_skipped_count || 0);
const resultCount = (job: WorkJob) =>
  job.operation === 'quality'
    ? job.passed_count || 0
    : ['detect', 'filter', 'img_filter', 'box_clean'].includes(job.operation) && job.passed_count !== undefined
      ? job.passed_count
      : completedResults(job);
const resultKind = (operation?: WorkJob['operation']) =>
  operation === 'attributes' || operation === 'caption'
    ? 'image'
    : operation === 'preannotate' || operation === 'ai_review' || operation === 'box_clean'
      ? 'detection'
      : undefined;
const canFollow = (source?: string) => source === 'originals' || source === 'crops' || source === 'all' || source === 'uploads';
const imageKinds = { originals: '项目原图', crops: '项目裁剪图', all: '全部项目图片', uploads: '仅接收新上传及抓图' };
const workflowTime = (date: string) => new Date(date).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });

function upstreamCropLabels(steps: WorkflowStep[], stepIndex: number, models: ModelService[], fallback: string[] = []): string[] {
  const crop = steps.slice(0, stepIndex).findLast(s => s.operation === 'crop');
  if (crop?.labels?.length) return crop.labels;
  if (crop?.model_id) return models.find(m => m.id === crop.model_id)?.labels ?? fallback;
  return fallback;
}

function HistoryConfig({ config }: { config: WorkflowHistoryConfig }) {
  const cropEntry = config.steps.findIndex(step => step.id === config.uploaded_crop_job_id);
  const source = config.source_job_id
    ? `已有任务输出 · ${config.source_job_id}`
    : config.image_kind
      ? imageKinds[config.image_kind]
      : '未记录';
  const normalized = normalizeGraph(
    config.steps.map((s, idx) => ({ ...s, node_key: s.node_key || `step_${idx}` })),
    config.edges,
  );
  return (
    <Space orientation="vertical" style={{ width: '100%' }} size="middle">
      <Descriptions
        size="small"
        column={1}
        items={[
          { key: 'time', label: '创建时间', children: workflowTime(config.created_at) },
          { key: 'source', label: '输入图片', children: <>{source}{config.source_job_id === undefined && config.image_kind && '（旧记录未保存来源任务 ID）'}</> },
          { key: 'follow', label: '创建时自动接收新图', children: config.follow_uploads == null ? '未记录' : config.follow_uploads ? '开启' : '关闭' },
          ...(cropEntry < 0 ? [] : [{ key: 'entry', label: '上传裁剪图入口', children: `第 ${cropEntry + 1} 步 · ${operations[config.steps[cropEntry].operation]}` }]),
        ]}
      />
      <WorkflowCanvas steps={normalized.steps} edges={normalized.edges} models={[]} inputLabel={source} readonly />
      {config.steps.map((step, index) => {
        const detection = ['crop', 'detect', 'preannotate'].includes(step.operation);
        const review = step.operation === 'ai_review';
        const isModel = MODEL_OPERATIONS.has(step.operation);
        const promptLabel = review
          ? '补充审核要求'
          : step.operation === 'quality'
            ? '图片质量要求'
            : step.operation === 'filter'
              ? '筛选提示词'
              : step.operation === 'caption'
                ? '描述提示词'
                : step.operation === 'attributes'
                  ? '属性补充提示词'
                  : '检测提示词';
        const attributes =
          step.attribute_templates?.map(template => ({ name: template.name, config: template.config })) ??
          (step.attribute_schema ? [{ name: '统一使用一套属性', config: step.attribute_schema }] : []);
        return (
          <Card key={step.id} size="small" title={`${index + 1}. ${step.title || operations[step.operation]}`}>
            <Descriptions
              size="small"
              column={1}
              items={[
                {
                  key: 'exec',
                  label: isModel ? '模型' : '执行引擎',
                  children: isModel
                    ? `${step.model_name || '未记录'}${step.model_revision == null ? '' : ` · 配置版本 ${step.model_revision}`}`
                    : step.model_name || operations[step.operation],
                },
                ...(detection || review ? [{ key: 'labels', label: review ? '审核类别' : '检测类别', children: step.labels?.join('、') || (review ? '全部项目类别' : '模型默认类别') }] : []),
                ...(detection ? [{ key: 'confidence', label: '置信度', children: step.confidence ?? '未记录' }] : []),
                ...(review ? [{ key: 'review', label: '模型框自动通过置信度', children: step.auto_approve_confidence ?? '未记录' }] : []),
                ...(step.operation === 'crop'
                  ? [
                      { key: 'expansion', label: '每侧外扩比例', children: step.expansion == null ? '未记录' : `${Math.round(step.expansion * 10000) / 100}%` },
                      { key: 'min', label: '裁剪图最小长边', children: step.min_crop_long_edge == null ? '未记录' : `${step.min_crop_long_edge} px` },
                      { key: 'boxes', label: '检测框映射到切图前的图片', children: step.save_input_boxes == null ? '未记录' : step.save_input_boxes ? '开启' : '关闭' },
                    ]
                  : []),
                ...(isModel && step.default_prompt && !(step.operation === 'caption' && step.prompt)
                  ? [{ key: 'default', label: '模型默认提示词', children: <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{step.default_prompt}</span> }]
                  : []),
                ...(isModel && (step.prompt || !detection)
                  ? [{ key: 'prompt', label: promptLabel, children: <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{step.prompt || (step.operation === 'caption' ? '使用模型默认设置' : '未填写')}</span> }]
                  : []),
                ...(step.operation === 'attributes'
                  ? [{ key: 'attribute-prompt', label: '属性核心提示词', children: <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{step.attribute_prompt || '使用系统默认提示词'}</span> }]
                  : []),
                ...(step.branch_config
                  ? [{ key: 'branches', label: '分流分支', children: step.branch_config.branches.map(b => b.name).join('、') + ' + 默认兜底' }]
                  : []),
                ...(step.img_filter_config
                  ? [{ key: 'img-filter', label: '图像过滤配置', children: JSON.stringify(step.img_filter_config) }]
                  : []),
                ...(step.box_clean_config
                  ? [{ key: 'box-clean', label: '框清洗配置', children: JSON.stringify(step.box_clean_config) }]
                  : []),
                ...(step.img_transform_config
                  ? [{ key: 'img-transform', label: '图像变换配置', children: JSON.stringify(step.img_transform_config) }]
                  : []),
              ]}
            />
            {step.script_config?.code && (
              <pre style={{ marginTop: 8, padding: 10, background: 'var(--ui-code-bg, var(--ui-background))', border: '1px solid var(--ui-border)', borderRadius: 6, fontSize: 12, overflowX: 'auto' }}>
                {step.script_config.code}
              </pre>
            )}
            {attributes.map(({ name, config: attrCfg }, position) => (
              <details key={position}>
                <summary>{name} · {attributeTargets(attrCfg).join('、')}</summary>
                <ul>
                  {attrCfg.fields.map(field => (
                    <li key={field.key}>
                      <strong>{field.name}</strong> {field.instruction}{field.options.length > 0 && `（可选值 ${field.options.join('、')}）`}
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </Card>
        );
      })}
    </Space>
  );
}

export default function ProjectProcessingWorkflow({
  project,
  history = false,
  visible = true,
  loadedHistory,
  onLoadHistory,
  onConfigureAttributes,
  attributeRevision,
  onNavigate,
}: {
  project: Project;
  history?: boolean;
  visible?: boolean;
  loadedHistory?: { config: WorkflowHistoryConfig; seq: number };
  onLoadHistory?: (config: WorkflowHistoryConfig) => void;
  onConfigureAttributes: (labels?: string[]) => void;
  attributeRevision?: number;
  onNavigate: (tab: 'workflow' | 'history', sourceJobId?: string) => void;
}) {
  const { message } = App.useApp();
  const projectId = project.id;
  const [params] = useSearchParams();
  const selectedSource = params.get('source_job_id');

  const initialGraph = useMemo(() => {
    const first = createDefaultStep('crop', []);
    return normalizeGraph([first], []);
  }, []);

  const [workflowName, setWorkflowName] = useState('批量处理');
  const [source, setSource] = useState<string>('originals');
  const [followUploads, setFollowUploads] = useState(false);
  const [steps, setSteps] = useState<WorkflowStep[]>(initialGraph.steps);
  const [edges, setEdges] = useState<WorkflowEdge[]>(initialGraph.edges);

  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [templateId, setTemplateId] = useState<string>();
  const [templateAttributes, setTemplateAttributes] = useState<AttributeSchema>();
  const [draftBeforeTemplate, setDraftBeforeTemplate] = useState<{
    name: string;
    steps: WorkflowStep[];
    edges: WorkflowEdge[];
    attributes?: AttributeSchema;
  }>();
  const attributes = templateAttributes ?? (project.label_schema?.object_attributes as AttributeSchema | undefined);

  const [models, setModels] = useState<ModelService[]>([]);
  const [attributeDefault, setAttributeDefault] = useState<string>();
  const [qualityDefault, setQualityDefault] = useState<string>();
  const [sources, setSources] = useState<WorkJob[]>([]);
  const [sourcePage, setSourcePage] = useState(1);
  const [moreSources, setMoreSources] = useState(true);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [configRun, setConfigRun] = useState<WorkflowRun>();
  const [historyConfig, setHistoryConfig] = useState<WorkflowHistoryConfig>();
  const [configError, setConfigError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [openJob, setOpenJob] = useState<string>();
  const [details, setDetails] = useState<WorkDetail>();
  const [detailPage, setDetailPage] = useState(1);
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [dryRunByNode, setDryRunByNode] = useState<Map<string, WorkflowDryRunNodeReport>>();
  const lastLoadedSeq = useRef(0);
  const pendingModelNames = useRef<Map<string, string>>(new Map());

  const applyHistoryConfig = (config: WorkflowHistoryConfig) => {
    const modelNameMap = new Map<string, string>();
    let extractedSchema: AttributeSchema | undefined;
    const mappedSteps: WorkflowStep[] = config.steps.map((s, idx) => {
      const nodeKey = s.node_key || `step_${idx}`;
      const matchedModelId =
        (s as unknown as WorkflowStep).model_id ||
        (s.model_name ? models.find(m => m.name === s.model_name)?.id : undefined) ||
        null;
      if (!matchedModelId && s.model_name) modelNameMap.set(nodeKey, s.model_name);
      if (s.attribute_schema && !s.attribute_templates && !extractedSchema) extractedSchema = s.attribute_schema;
      return {
        node_key: nodeKey,
        title: s.title,
        operation: s.operation,
        model_id: matchedModelId,
        prompt: s.prompt,
        attribute_prompt: s.attribute_prompt,
        labels: s.labels ? [...s.labels] : undefined,
        confidence: s.confidence,
        expansion: s.expansion,
        min_crop_long_edge: s.min_crop_long_edge,
        save_input_boxes: s.save_input_boxes,
        auto_approve_confidence: s.auto_approve_confidence,
        attribute_templates: s.attribute_templates ? structuredClone(s.attribute_templates) : undefined,
        branch_config: s.branch_config ? structuredClone(s.branch_config) : undefined,
        img_filter_config: s.img_filter_config ? structuredClone(s.img_filter_config) : undefined,
        box_clean_config: s.box_clean_config ? structuredClone(s.box_clean_config) : undefined,
        img_transform_config: s.img_transform_config ? structuredClone(s.img_transform_config) : undefined,
        script_config: s.script_config ? structuredClone(s.script_config) : undefined,
      };
    });
    pendingModelNames.current = modelNameMap;
    const norm = normalizeGraph(mappedSteps, config.edges ? structuredClone(config.edges) : undefined);
    setWorkflowName(config.name || '批量处理');
    setSteps(norm.steps);
    setEdges(norm.edges);
    if (config.source_job_id) {
      setSource(config.source_job_id);
      setFollowUploads(false);
    } else if (config.image_kind) {
      setSource(config.image_kind);
      setFollowUploads(config.image_kind === 'uploads' ? true : !!config.follow_uploads);
    }
    setTemplateAttributes(extractedSchema);
    setTemplateId(undefined);
    setDryRunByNode(undefined);
    setError('');
    message.success('已载入历史工作流配置，请核对模型与输入范围后启动');
  };

  useEffect(() => {
    if (history || !loadedHistory || loadedHistory.seq === lastLoadedSeq.current) return;
    lastLoadedSeq.current = loadedHistory.seq;
    applyHistoryConfig(loadedHistory.config);
  }, [history, loadedHistory]);

  useEffect(() => {
    if (history || !models.length || !pendingModelNames.current.size) return;
    setSteps(prev =>
      prev.map(step => {
        if (step.model_id || !step.node_key) return step;
        const wantedName = pendingModelNames.current.get(step.node_key);
        const found = wantedName ? models.find(m => m.name === wantedName) : undefined;
        if (found) {
          pendingModelNames.current.delete(step.node_key);
          return { ...step, model_id: found.id };
        }
        return step;
      }),
    );
  }, [history, models]);

  const partialSource = sources.find(job => job.id === source && (job.counts.failed || job.counts.cancelled));

  useEffect(() => {
    if (history) return;
    let active = true;
    getWorkflowTemplates()
      .then(rows => { if (active) setTemplates(rows); })
      .catch(cause => { if (active) setError(errorText(cause)); });
    return () => { active = false; };
  }, [projectId, history]);

  useEffect(() => {
    if (history) return;
    let active = true;
    getModelServices({ enabled: true })
      .then(rows => { if (active) setModels(rows); })
      .catch(cause => { if (active) setError(errorText(cause)); });
    getPromptDefaults()
      .then(defaults => {
        if (active) {
          setAttributeDefault(defaults.attributes);
          setQualityDefault(defaults.quality);
        }
      })
      .catch(() => { if (active) setError('读取默认提示词失败，请刷新任务与模型后重试。'); });
    return () => { active = false; };
  }, [revision, history]);

  useEffect(() => {
    if (history || !selectedSource) return;
    let active = true;
    getWork(projectId, selectedSource)
      .then(job => {
        if (active) {
          setSources(current => [job, ...current.filter(row => row.id !== job.id)]);
          setSource(job.id);
          setFollowUploads(false);
        }
      })
      .catch(cause => { if (active) setError(errorText(cause)); });
    return () => { active = false; };
  }, [projectId, selectedSource, history]);

  useEffect(() => {
    if (history) return;
    let active = true;
    getWorkJobs(projectId, sourcePage, true)
      .then(rows => {
        if (active) {
          setSources(current => [
            ...new Map(
              [...(sourcePage === 1 ? current.filter(row => row.id === source) : current), ...rows].map(row => [row.id, row]),
            ).values(),
          ]);
          setMoreSources(rows.length === 20);
        }
      })
      .catch(cause => { if (active) setError(errorText(cause)); });
    return () => { active = false; };
  }, [projectId, sourcePage, revision, history]);

  useEffect(() => { if (history && visible) setPage(1); }, [history, visible]);

  useEffect(() => {
    if (!history || !visible) return;
    let active = true, pending = false, shouldPoll = true;
    const read = async () => {
      if (pending) return;
      pending = true;
      try {
        const rows = await getWorkflows(projectId, page);
        if (active) setRuns(rows);
        shouldPoll = rows.some(
          run =>
            run.steps.some(job => running(job) || job.follow_uploads) ||
            (run.steps.some(waitingToStart) && !run.steps.some(job => job.counts.failed || job.counts.cancelled)),
        );
        if (openJob) {
          const detail = await getWork(projectId, openJob, detailPage);
          if (active) setDetails(detail);
        }
      } catch (cause) {
        if (active) setError(errorText(cause));
      } finally {
        pending = false;
      }
    };
    void read();
    const timer = setInterval(() => { if (shouldPoll && !document.hidden) void read(); }, 3500);
    return () => { active = false; clearInterval(timer); };
  }, [projectId, page, revision, openJob, detailPage, history, visible]);

  useEffect(() => {
    if (!configRun) return;
    let active = true;
    getWorkflowHistoryConfig(projectId, configRun.id)
      .then(config => { if (active) setHistoryConfig(config); })
      .catch(cause => { if (active) setConfigError(errorText(cause)); });
    return () => { active = false; };
  }, [projectId, configRun]);

  const validateBeforeSaveOrRun = (): boolean => {
    if (!workflowName.trim()) {
      setError('请填写工作流名称。');
      return false;
    }
    const invalidModelIdx = steps.findIndex(
      step =>
        MODEL_OPERATIONS.has(step.operation) &&
        !models.some(m => m.id === step.model_id && m.enabled !== false && !isModelOffline(m)),
    );
    if (invalidModelIdx >= 0) {
      setError(`第 ${invalidModelIdx + 1} 步「${steps[invalidModelIdx].title || operations[steps[invalidModelIdx].operation]}」尚未选择可用在线模型。`);
      return false;
    }
    if (steps.some(step => step.operation === 'attributes' && step.attribute_templates == null) && !attributes) {
      setError('请先保存目标与属性配置。');
      return false;
    }
    return true;
  };

  const submit = async () => {
    if (!validateBeforeSaveOrRun()) return;
    setBusy(true);
    setError('');
    try {
      const kind = (['originals', 'crops', 'all', 'uploads'] as const).find(value => value === source);
      await createWorkflow(projectId, {
        name: workflowName.trim(),
        image_kind: kind || 'all',
        source_job_id: kind ? undefined : source,
        follow_uploads: canFollow(kind) && !!followUploads,
        attribute_schema: templateAttributes,
        steps,
        edges,
      });
      onNavigate('history');
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const loadTemplate = (id: string) => {
    const template = templates.find(row => row.id === id);
    if (!template) return;
    if (!draftBeforeTemplate) {
      setDraftBeforeTemplate({
        name: workflowName,
        steps: structuredClone(steps),
        edges: structuredClone(edges),
        attributes: templateAttributes,
      });
    }
    const norm = normalizeGraph(structuredClone(template.steps), template.edges ? structuredClone(template.edges) : undefined);
    setWorkflowName(template.name);
    setSteps(norm.steps);
    setEdges(norm.edges);
    setDryRunByNode(undefined);
    setTemplateId(id);
    setTemplateAttributes(template.attribute_schema ?? undefined);
  };

  const undoTemplate = () => {
    setTemplateId(undefined);
    if (!draftBeforeTemplate) return;
    setWorkflowName(draftBeforeTemplate.name);
    setSteps(draftBeforeTemplate.steps);
    setEdges(draftBeforeTemplate.edges);
    setTemplateAttributes(draftBeforeTemplate.attributes);
    setDraftBeforeTemplate(undefined);
    setDryRunByNode(undefined);
  };

  const saveTemplate = async () => {
    if (!validateBeforeSaveOrRun()) return;
    const needsAttributes = steps.some(step => step.operation === 'attributes' && step.attribute_templates == null);
    setBusy(true);
    setError('');
    try {
      const saved = await saveWorkflowTemplate({
        name: workflowName.trim(),
        steps,
        edges,
        attribute_schema: needsAttributes ? attributes : undefined,
      });
      setTemplates(rows => [...rows.filter(r => r.id !== saved.id), saved]);
      setTemplateId(saved.id);
      message.success(`已保存工作流模板「${saved.name}」`);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await action();
      setRevision(value => value + 1);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const inspect = (id: string) => {
    setOpenJob(id);
    setDetails(undefined);
    setDetailPage(1);
  };

  const resultLink = (job: WorkJob) => {
    const search = new URLSearchParams({ work_job_id: job.id });
    const kind = resultKind(job.operation);
    if (kind) search.set('kind', kind);
    if (job.operation === 'crop' || job.operation === 'img_transform') search.set('image_kind', 'crops');
    return `/projects/${projectId}/images?${search}`;
  };

  const sourceOptions = [
    {
      label: '项目图片',
      options: [
        { value: 'originals', label: '项目原图' },
        { value: 'crops', label: '项目裁剪图' },
        { value: 'all', label: '全部项目图片' },
        { value: 'uploads', label: '仅接收新上传及抓图' },
      ],
    },
    {
      label: '已有任务输出',
      options: sources.map(job => ({
        value: job.id,
        disabled: running(job) || job.follow_uploads || !job.counts.completed,
        label: `${job.created_at ? new Date(job.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : job.id.slice(0, 8)} · ${job.batch_count ? `合并 ${job.batch_count} 批 · ` : ''}${job.total.toLocaleString('zh-CN')} 张输入 · ${job.target_labels?.join('、') || operations[job.operation as WorkflowOperation] || '切图'}`,
      })),
    },
  ];

  const currentSourceLabel =
    source in imageKinds
      ? imageKinds[source as keyof typeof imageKinds]
      : `任务输出 · ${source.slice(0, 8)}`;

  const dryRunImageKind: 'originals' | 'crops' | 'all' =
    source === 'crops' ? 'crops' : source === 'all' ? 'all' : 'originals';

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      {error && <Alert showIcon type="error" title={error} closable onClose={() => setError('')} />}

      {!history && (
        <Card title="新建处理工作流" size="small">
          <p className="workspace-help">
            支持通过自然语言由 AI 自动编排，也可在下方流程图中混排「AI 模型 + 免模型规则 + 条件分流 + Python 安全脚本」。每张图片完成后即时流向下游节点。
          </p>

          <Space wrap style={{ marginBottom: 14 }}>
            <Select
              aria-label="载入工作流模板"
              placeholder="载入工作流模板"
              value={templateId}
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: 230 }}
              disabled={busy}
              options={templates.map(row => ({ value: row.id, label: row.name }))}
              onSelect={loadTemplate}
              onClear={undoTemplate}
            />
            {draftBeforeTemplate && <Button disabled={busy} onClick={undoTemplate}>撤回载入</Button>}
            <Button disabled={busy} onClick={() => void saveTemplate()}>保存为模板</Button>
            <Link to="/projects/workflow-templates">管理模板</Link>
            <Typography.Text type="secondary">模板跨项目共享，载入后可继续用 AI 或点击节点微调。</Typography.Text>
          </Space>

          <Space wrap align="start" style={{ marginBottom: 8, width: '100%' }}>
            <div>
              <label htmlFor="wf-name-input" style={{ display: 'block', marginBottom: 4, color: 'var(--ui-muted)', fontSize: 13 }}>
                * 工作流名称
              </label>
              <Input
                id="wf-name-input"
                aria-label="工作流名称"
                style={{ width: 220 }}
                maxLength={100}
                disabled={busy}
                value={workflowName}
                onChange={e => setWorkflowName(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="wf-source-select" style={{ display: 'block', marginBottom: 4, color: 'var(--ui-muted)', fontSize: 13 }}>
                * 输入图片
              </label>
              <Select
                id="wf-source-select"
                aria-label="输入图片"
                showSearch
                optionFilterProp="label"
                style={{ width: 340, maxWidth: '100%' }}
                disabled={busy}
                value={source}
                options={sourceOptions}
                onChange={val => {
                  setSource(val);
                  if (val === 'uploads' || !canFollow(val)) setFollowUploads(val === 'uploads');
                }}
              />
            </div>
            <div style={{ paddingTop: 24 }}>
              <Space wrap>
                <Button onClick={() => { setSourcePage(1); setRevision(v => v + 1); }} disabled={busy}>
                  刷新任务与模型
                </Button>
                {moreSources && (
                  <Button onClick={() => setSourcePage(v => v + 1)} disabled={busy}>
                    读取更早的切图任务
                  </Button>
                )}
              </Space>
            </div>
          </Space>

          <div style={{ marginBottom: 14 }}>
            <Checkbox
              disabled={busy || !canFollow(source) || source === 'uploads'}
              checked={followUploads}
              onChange={e => setFollowUploads(e.target.checked)}
            >
              自动加入新上传及抓图
            </Checkbox>
            <Typography.Text type="secondary" style={{ fontSize: 12, marginInlineStart: 8 }}>
              {canFollow(source)
                ? '勾选后，符合输入类型的新上传图片和抓图也会实时流入此工作流。'
                : '使用已有任务输出时，以选中的任务结果为输入。'}
            </Typography.Text>
          </div>

          {partialSource && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 14 }}
              title="此来源有失败或取消项，本次仅使用已生成的结果。"
            />
          )}

          <WorkflowGraphBuilder
            projectId={projectId}
            workflowName={workflowName}
            steps={steps}
            edges={edges}
            models={models}
            inputLabel={currentSourceLabel}
            attributeDefault={attributeDefault}
            qualityDefault={qualityDefault}
            attributes={attributes}
            attributeLabel={templateAttributes ? '模板' : '项目'}
            attributeRevision={attributeRevision}
            dryRunByNode={dryRunByNode}
            disabled={busy}
            onChangeGraph={next => {
              if (next.name !== undefined) setWorkflowName(next.name);
              setSteps(next.steps);
              setEdges(next.edges);
              setDryRunByNode(undefined);
            }}
            attributeAction={(idx, category) =>
              !category && templateAttributes ? (
                <Button onClick={() => setTemplateAttributes(undefined)}>改用项目属性</Button>
              ) : (
                <Button
                  onClick={() =>
                    onConfigureAttributes(
                      upstreamCropLabels(steps, idx, models, sources.find(j => j.id === source)?.target_labels),
                    )
                  }
                >
                  {category ? '管理属性模板' : '配置目标与属性'}
                </Button>
              )
            }
          />

          <Space wrap style={{ marginTop: 16 }}>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={busy}
              onClick={() => void submit()}
            >
              启动工作流
            </Button>
            <Button
              icon={<ExperimentOutlined />}
              disabled={busy || source === 'uploads'}
              onClick={() => {
                if (validateBeforeSaveOrRun()) setDryRunOpen(true);
              }}
            >
              样本试运行（不落库）
            </Button>
          </Space>

          <WorkflowDryRunModal
            open={dryRunOpen}
            projectId={projectId}
            workflow={{ name: workflowName, steps, edges, attribute_schema: templateAttributes }}
            imageKind={dryRunImageKind}
            sourceJobId={source in imageKinds ? undefined : source}
            onClose={() => setDryRunOpen(false)}
            onResult={res => setDryRunByNode(new Map(res.nodes.map(n => [n.node_key, n])))}
          />
        </Card>
      )}

      {history && (
        <Card
          title="工作流记录"
          size="small"
        >
          {!runs.length && <p className="workspace-help">暂无工作流记录</p>}
          <Space orientation="vertical" style={{ width: '100%' }}>
            {runs.map(run => (
              <Card
                key={run.id}
                size="small"
                title={
                  <Button
                    type="link"
                    size="small"
                    style={{ paddingInline: 0 }}
                    title="查看工作流流程图与配置"
                    onClick={() => {
                      setHistoryConfig(undefined);
                      setConfigError('');
                      setConfigRun(run);
                    }}
                  >
                    {run.name}
                  </Button>
                }
                extra={
                  <Space wrap>
                    {run.steps[0]?.created_at && (
                      <Typography.Text type="secondary">{workflowTime(run.steps[0].created_at)}</Typography.Text>
                    )}
                    {(run.steps[0]?.workflow_can_follow ?? run.steps[0]?.workflow_streaming) && (
                      <Button
                        size="small"
                        disabled={busy}
                        onClick={() => void act(() => followWorkUploads(projectId, run.steps[0].id, !run.steps[0].follow_uploads))}
                      >
                        {run.steps[0].follow_uploads ? '停止接收新图' : '继续接收新图'}
                      </Button>
                    )}
                    {run.steps[0]?.workflow_can_route_uploaded_crops && (
                      <Select
                        size="small"
                        aria-label="上传裁剪图入口"
                        prefix="裁剪图入口："
                        placeholder="选择开始步骤"
                        allowClear
                        style={{ minWidth: 205 }}
                        disabled={busy}
                        value={run.steps[0].uploaded_crop_job_id ?? undefined}
                        options={run.steps.map((step, index) => ({
                          value: step.id,
                          label: `${index + 1}. ${step.title || operations[step.operation as WorkflowOperation]}`,
                          disabled: step.workflow_cancelled,
                        }))}
                        onChange={value =>
                          void act(() => followWorkUploads(projectId, run.steps[0].id, !!run.steps[0].follow_uploads, value ?? null))
                        }
                      />
                    )}
                    {run.steps.some(job => running(job) || job.follow_uploads || waitingToStart(job)) && (
                      <Popconfirm
                        title="取消整个工作流？"
                        description="停止所有步骤及自动接收新图，已完成的结果会保留。"
                        okText="确认取消"
                        cancelText="继续运行"
                        onConfirm={() => act(() => cancelWorkflow(projectId, run.id))}
                      >
                        <Button size="small" danger disabled={busy}>取消整个工作流</Button>
                      </Popconfirm>
                    )}
                    {!run.steps.some(job => running(job) || job.follow_uploads || waitingToStart(job)) && (
                      <Popconfirm
                        title="删除这条工作流记录？"
                        description="已生成的裁剪图和标注会保留。"
                        okText="删除记录"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        onConfirm={() =>
                          act(async () => {
                            await deleteWorkflow(projectId, run.id);
                            if (run.steps.some(job => job.id === openJob)) setOpenJob(undefined);
                            if (runs.length === 1 && page > 1) setPage(page - 1);
                          })
                        }
                      >
                        <Button size="small" danger disabled={busy}>删除工作流</Button>
                      </Popconfirm>
                    )}
                  </Space>
                }
              >
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={run.steps}
                  pagination={false}
                  scroll={{ x: 780 }}
                  columns={[
                    {
                      title: '步骤',
                      render: (_, job, index) => `${index + 1}. ${job.title || operations[job.operation as WorkflowOperation] || job.operation}`,
                    },
                    {
                      title: '模型 / 引擎',
                      render: (_, job) => job.model_name || operations[job.operation as WorkflowOperation] || '—',
                    },
                    {
                      title: '状态',
                      render: (_, job, index) => {
                        const active = running(job);
                        const needsRetry = needsManualRetry(job);
                        const cancelled = job.workflow_cancelled && !job.follow_uploads && (job.counts.cancelled > 0 || !job.total);
                        const waiting =
                          !job.workflow_cancelled &&
                          (waitingToStart(job) || run.steps.slice(0, index).some(step => running(step) || step.follow_uploads));
                        const status = active
                          ? job.counts.pending || job.counts.running
                            ? '处理中'
                            : '等待自动重试'
                          : cancelled
                            ? '已取消'
                            : needsRetry
                              ? '需重试'
                              : job.follow_uploads
                                ? '等待新图片'
                                : waiting
                                  ? '等待上游结果'
                                  : '已完成';
                        return (
                          <Space wrap>
                            <Tag color={active ? 'processing' : cancelled ? 'default' : needsRetry ? 'error' : waiting || job.follow_uploads ? 'processing' : 'success'}>
                              {status}
                            </Tag>
                            <span>
                              完成 {completedResults(job)} / {job.total}
                              {job.attribute_skipped_count ? ` · 跳过 ${job.attribute_skipped_count}` : ''}
                              {job.quality_counts
                                ? ` · 合格 ${job.quality_counts.passed} · 已删除 ${job.quality_counts.deleted}`
                                : job.passed_count !== undefined
                                  ? ` · 命中 ${job.passed_count}`
                                  : ''}
                              {job.retrying_count ? ` · 待自动重试 ${job.retrying_count}` : ''}
                              {(job.counts.failed || 0) > (job.retrying_count || 0)
                                ? ` · 失败 ${(job.counts.failed || 0) - (job.retrying_count || 0)}`
                                : ''}
                            </span>
                          </Space>
                        );
                      },
                    },
                    {
                      title: '操作',
                      render: (_, job) => (
                        <Space size="small" wrap>
                          {!!job.total && <Button type="link" size="small" onClick={() => inspect(job.id)}>查看明细</Button>}
                          {resultCount(job) > 0 && <Link to={resultLink(job)}>查看结果图片</Link>}
                          {job.operation === 'quality' && resultCount(job) > 0 && (
                            <Link to={`/annotations?project_id=${projectId}&work_job_id=${job.id}`}>标注合格图片</Link>
                          )}
                          {resultCount(job) > 0 && <Link to={resultLink(job).replace('/images?', '/deliver?')}>冻结本步结果</Link>}
                          {resultCount(job) > 0 && !running(job) && !job.follow_uploads && (
                            <Button type="link" size="small" onClick={() => onNavigate('workflow', job.id)}>作为新流程输入</Button>
                          )}
                          {running(job) && (
                            <Popconfirm
                              title="取消这一步？"
                              description={job.workflow_streaming ? '已完成的结果保留，下游步骤可继续处理已收到的图片。' : '已完成的结果保留。'}
                              onConfirm={() => act(() => workAction(projectId, job.id, 'cancel'))}
                              okText="取消处理"
                              cancelText="继续处理"
                            >
                              <Button type="link" size="small" danger disabled={busy}>取消处理</Button>
                            </Popconfirm>
                          )}
                          {needsManualRetry(job) && (
                            <Button type="link" size="small" disabled={busy} onClick={() => void act(() => workAction(projectId, job.id, 'retry'))}>
                              重试
                            </Button>
                          )}
                        </Space>
                      ),
                    },
                  ]}
                />
              </Card>
            ))}
          </Space>
          <CursorPager page={page} hasMore={runs.length >= 10}
            onPrev={() => setPage(value => value - 1)} onNext={() => setPage(value => value + 1)} />
        </Card>
      )}

      {history && openJob && (
        <Card size="small" title="步骤明细" extra={<Button size="small" onClick={() => { setOpenJob(undefined); setDetails(undefined); }}>关闭明细</Button>}>
          {details?.quality_requirement && (
            <Alert type="info" showIcon title="本步图片质量要求" description={details.quality_requirement} style={{ marginBottom: 12 }} />
          )}
          <Table
            size="small"
            rowKey="id"
            loading={!details}
            dataSource={details?.items || []}
            scroll={{ x: 620 }}
            pagination={{ current: detailPage, pageSize: 20, total: details?.total || 0, onChange: setDetailPage, showSizeChanger: false }}
            columns={[
              {
                title: '图片',
                dataIndex: 'original_name',
                render: (name, item) => {
                  const kind = resultKind(details?.operation);
                  if (!item.asset_id || !kind || !details || item.result?.attribute_skipped || (kind === 'image' && item.status !== 'completed')) {
                    return name;
                  }
                  const search = new URLSearchParams({ kind });
                  if (item.status === 'completed') search.set('work_job_id', details.id);
                  return <Link to={`/annotate/${item.asset_id}?${search}`}>{name || '打开标注'}</Link>;
                },
              },
              {
                title: '状态',
                render: (_, item) =>
                  item.status === 'completed'
                    ? item.result?.deleted
                      ? '不合格 · 已删除'
                      : details?.operation === 'quality'
                        ? '合格'
                        : item.result?.attribute_skipped
                          ? '已跳过'
                          : item.result?.passed === false
                            ? '未命中 / 拦截'
                            : '已完成'
                    : item.auto_retry
                      ? '待自动重试'
                      : ({ pending: '等待', running: '处理中', failed: '失败', cancelled: '已取消' } as Record<string, string>)[item.status],
              },
              {
                title: '结果',
                render: (_, item) => {
                  const template = details?.attribute_templates?.find(row => row.id === item.result?.attribute_template_id);
                  const schema = template?.config || details?.attribute_schema;
                  if (item.error) return item.error;
                  if (item.result?.attributes && schema) {
                    return (
                      <Space wrap>
                        {template && <Tag color="blue">{template.name}</Tag>}
                        {schema.fields.map(field => {
                          const value = item.result?.attributes?.[field.key];
                          return (
                            <Space key={field.key} orientation="vertical">
                              <Tag title={value?.reason}>{field.name}：{value?.value ?? '无法判断'}</Tag>
                              <AttributeProbabilities probabilities={value?.probabilities} />
                            </Space>
                          );
                        })}
                      </Space>
                    );
                  }
                  return (
                    item.result?.description ||
                    item.result?.reason ||
                    (item.result?.detected_count === undefined
                      ? '—'
                      : details?.operation === 'crop' || details?.operation === 'img_transform'
                        ? `生成 ${item.result.detected_count} 张衍生图${item.result.source_annotation_id ? ' · 输入图已保存检测框' : item.result.source_annotation_preserved ? ' · 保留输入图已有检测标注' : ''}`
                        : `处理后 ${item.result.detected_count} 个目标`)
                  );
                },
              },
            ]}
          />
        </Card>
      )}

      {history && (
        <Modal
          title={`工作流配置 · ${configRun?.name || ''}`}
          open={visible && !!configRun}
          width={860}
          onCancel={() => setConfigRun(undefined)}
          footer={
            <Space>
              <Button onClick={() => setConfigRun(undefined)}>关闭</Button>
              {historyConfig && (
                <Button
                  type="primary"
                  onClick={() => {
                    const cfg = historyConfig;
                    setConfigRun(undefined);
                    if (onLoadHistory) onLoadHistory(cfg);
                    else applyHistoryConfig(cfg);
                  }}
                >
                  载入到新建工作流
                </Button>
              )}
            </Space>
          }
          styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
          loading={!historyConfig && !configError}
        >
          {configError ? <Alert type="error" showIcon title={configError} /> : historyConfig && <HistoryConfig config={historyConfig} />}
        </Modal>
      )}
    </Space>
  );
}
