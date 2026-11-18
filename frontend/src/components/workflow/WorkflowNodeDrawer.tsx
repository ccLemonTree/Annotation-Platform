import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Divider,
  Drawer,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import { DeleteOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import {
  DEFAULT_DETECTION_CONFIDENCE,
  isModelOffline,
  type ModelService,
} from '../../api/modelServices';
import {
  attributeTargets,
  builtinAttributeTemplates,
  getAttributeTemplates,
  resolvedAttributeTemplates,
  type AttributeSchema,
  type AttributeTemplate,
} from '../../api/objectAttributes';
import {
  aiGenerateWorkflowScript,
  type BranchCase,
  type WorkflowEdge,
  type WorkflowOperation,
  type WorkflowStep,
} from '../../api/projectWork';
import PromptEditor from '../PromptEditor';
import JsonInput from '../JsonInput';
import {
  DEFAULT_PYTHON_SCRIPT,
  MODEL_OPERATIONS,
  NODE_MENU_GROUPS,
  SCRIPT_SNIPPETS,
  WORKFLOW_OPERATIONS,
  createDefaultStep,
} from './workflowGraph';

const builtinTargets: Record<string, string[]> = {
  builtin_person: ['person', 'pedestrian', 'human', '人', '人员', '行人'],
  builtin_vehicle: ['car', 'truck', 'bus', 'van', 'vehicle', '车', '车辆', '汽车', '卡车', '公交车'],
  builtin_object: ['object', '物品', '物体'],
};
const categoryTemplates = builtinAttributeTemplates.map(template => ({
  ...template,
  config: { ...template.config, target_label: builtinTargets[template.id] || template.config.target_label },
}));

const errorText = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail
    : 'AI 脚本生成失败，请检查编排模型或稍后重试';

function collectDescendants(startKey: string, edges: WorkflowEdge[]): Set<string> {
  const visited = new Set<string>();
  const stack = [startKey];
  while (stack.length) {
    const curr = stack.pop()!;
    if (visited.has(curr)) continue;
    visited.add(curr);
    for (const e of edges) {
      if (e.source === curr && !visited.has(e.target)) stack.push(e.target);
    }
  }
  return visited;
}

export default function WorkflowNodeDrawer({
  open,
  step,
  stepIndex,
  allSteps,
  edges,
  models,
  projectId,
  attributeDefault,
  qualityDefault,
  attributes,
  attributeLabel,
  attributeRevision = 0,
  attributeAction,
  disabled,
  onClose,
  onUpdateStep,
  onUpdateIncomingEdges,
  onDeleteNode,
}: {
  open: boolean;
  step?: WorkflowStep;
  stepIndex: number;
  allSteps: WorkflowStep[];
  edges: WorkflowEdge[];
  models: ModelService[];
  projectId?: string;
  attributeDefault?: string;
  qualityDefault?: string;
  attributes?: AttributeSchema;
  attributeLabel: string;
  attributeRevision?: number;
  attributeAction?: (category: boolean) => ReactNode;
  disabled?: boolean;
  onClose: () => void;
  onUpdateStep: (updated: WorkflowStep) => void;
  onUpdateIncomingEdges: (nodeKey: string, sources: { source: string; source_handle: string }[]) => void;
  onDeleteNode: (nodeKey: string) => void;
}) {
  const [customTemplates, setCustomTemplates] = useState<AttributeTemplate[]>([]);
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState('');
  const [aiNote, setAiNote] = useState('');
  const [renameText, setRenameText] = useState(() =>
    step?.operation === 'box_clean' ? JSON.stringify(step.box_clean_config?.rename_map || {}, null, 2) : '{}',
  );
  const [renameErr, setRenameErr] = useState('');

  useEffect(() => {
    let active = true;
    getAttributeTemplates()
      .then(rows => { if (active) setCustomTemplates(rows); })
      .catch(() => {});
    return () => { active = false; };
  }, [attributeRevision]);

  const defaults = useMemo(
    () =>
      resolvedAttributeTemplates(customTemplates).map(template =>
        template.id.startsWith('builtin_') && !customTemplates.some(row => row.id === template.id)
          ? categoryTemplates.find(row => row.id === template.id) ?? template
          : template,
      ),
    [customTemplates],
  );

  if (!step) return null;
  const nodeKey = step.node_key!;
  const op = step.operation;
  const isModelOp = MODEL_OPERATIONS.has(op);
  const isDetection = op === 'crop' || op === 'detect' || op === 'preannotate';
  const isReview = op === 'ai_review';
  const isQuality = op === 'quality';
  const requiresPrompt = op === 'filter' || isQuality;

  const modelCandidates = models.filter(
    m =>
      m.enabled !== false &&
      m.kind === (isDetection ? 'detection' : 'caption') &&
      (requiresPrompt || isReview
        ? m.protocol === 'openai_chat'
        : op === 'attributes'
          ? ['openai_chat', 'jev_decide'].includes(m.protocol)
          : m.protocol !== 'jev_decide'),
  );
  const currentModel = modelCandidates.find(m => m.id === step.model_id);
  const selectedTemplates = step.attribute_templates;
  const categoryMode = selectedTemplates != null;
  const availableTemplates = new Map([...defaults, ...(selectedTemplates || [])].map(t => [t.id, t]));
  const jev = currentModel?.protocol === 'jev_decide';
  const hasPrompt =
    isModelOp &&
    (requiresPrompt || op === 'caption' || currentModel?.protocol === 'openai_chat' || currentModel?.endpoint === '/detect/grounding');
  const promptTitle = isReview
    ? '补充审核要求'
    : isQuality
      ? '图片质量要求'
      : op === 'filter'
        ? '筛选提示词'
        : op === 'caption'
          ? '描述提示词'
          : op === 'attributes'
            ? '属性补充提示词'
            : '检测提示词';

  // Upstream source selector (exclude self & descendants to prevent cycles)
  const forbiddenSources = collectDescendants(nodeKey, edges);
  const sourceOptions: { value: string; label: string }[] = [
    { value: '__input__::default', label: '起点 · 输入图片' },
  ];
  allSteps.forEach((candidate, idx) => {
    const cKey = candidate.node_key!;
    if (forbiddenSources.has(cKey)) return;
    const cTitle = `#${idx + 1} ${candidate.title || WORKFLOW_OPERATIONS[candidate.operation]}`;
    if (candidate.operation === 'branch') {
      for (const b of candidate.branch_config?.branches || []) {
        sourceOptions.push({ value: `${cKey}::${b.key}`, label: `${cTitle} → 分支「${b.name}」` });
      }
      sourceOptions.push({ value: `${cKey}::else`, label: `${cTitle} → 分支「默认/其他」` });
    } else {
      sourceOptions.push({ value: `${cKey}::default`, label: cTitle });
    }
  });

  const incomingEdges = edges.filter(e => e.target === nodeKey);
  const incomingValues = incomingEdges.length
    ? incomingEdges.map(e => `${e.source}::${e.source_handle}`)
    : ['__input__::default'];

  const changeOperation = (nextOp: WorkflowOperation) => {
    const fresh = createDefaultStep(nextOp, allSteps, models);
    onUpdateStep({ ...fresh, node_key: nodeKey });
  };

  const generateCodeWithAi = async () => {
    if (!aiPrompt.trim() || aiBusy) return;
    setAiBusy(true);
    setAiError('');
    setAiNote('');
    try {
      const res = await aiGenerateWorkflowScript(projectId, {
        prompt: aiPrompt.trim(),
        current_code: step.script_config?.code || '',
      });
      onUpdateStep({
        ...step,
        script_config: { ...(step.script_config || { timeout_seconds: 5, save_boxes: true, save_attributes: true, save_description: true }), code: res.code },
      });
      setAiNote(`AI（${res.orchestrator_model_name}）：${res.explanation}`);
    } catch (cause) {
      setAiError(errorText(cause));
    } finally {
      setAiBusy(false);
    }
  };

  const updateBranchCase = (idx: number, patch: Partial<BranchCase>) => {
    const list = [...(step.branch_config?.branches || [])];
    list[idx] = { ...list[idx], ...patch };
    onUpdateStep({ ...step, branch_config: { first_match: step.branch_config?.first_match ?? true, branches: list } });
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={580}
      title={`配置节点 #${stepIndex + 1} · ${step.title || WORKFLOW_OPERATIONS[op]}`}
      extra={
        allSteps.length > 1 && (
          <Popconfirm
            title="移除此节点？"
            description="移除后会自动桥接上下游连线。"
            okText="确认移除"
            cancelText="取消"
            onConfirm={() => onDeleteNode(nodeKey)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} disabled={disabled}>
              移除节点
            </Button>
          </Popconfirm>
        )
      }
    >
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap style={{ width: '100%' }} align="start">
          <div style={{ flex: 1, minWidth: 200 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>节点显示名称</Typography.Text>
            <Input
              aria-label="节点显示名称"
              value={step.title || ''}
              disabled={disabled}
              maxLength={100}
              placeholder={WORKFLOW_OPERATIONS[op]}
              onChange={e => onUpdateStep({ ...step, title: e.target.value })}
              style={{ marginTop: 4 }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>节点类型 / 处理方式</Typography.Text>
            <Select
              aria-label="节点处理方式"
              value={op}
              disabled={disabled}
              style={{ width: '100%', marginTop: 4 }}
              onChange={changeOperation}
              options={NODE_MENU_GROUPS.map(g => ({
                label: g.group,
                options: g.items.map(it => ({ value: it.operation, label: it.label })),
              }))}
            />
          </div>
        </Space>

        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>数据输入来源（支持选择上游步骤或条件分支）</Typography.Text>
          <Select
            mode="multiple"
            aria-label="节点上游输入来源"
            style={{ width: '100%', marginTop: 4 }}
            disabled={disabled}
            value={incomingValues}
            options={sourceOptions}
            onChange={(vals: string[]) => {
              if (!vals.length) return;
              const parsed = vals.map(v => {
                const [source, source_handle] = v.split('::');
                return { source, source_handle: source_handle || 'default' };
              });
              onUpdateIncomingEdges(nodeKey, parsed);
            }}
          />
        </div>

        <Divider style={{ margin: '4px 0' }} />

        {/* === 1. 模型推理节点配置 === */}
        {isModelOp && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <div>
              <Typography.Text strong>选择推理模型 *</Typography.Text>
              <Select
                showSearch
                aria-label="步骤推理模型"
                optionFilterProp="label"
                placeholder="请选择已启用的在线模型"
                style={{ width: '100%', marginTop: 6 }}
                disabled={disabled}
                value={step.model_id || undefined}
                onChange={id => {
                  const next = modelCandidates.find(r => r.id === id);
                  const nextStep = { ...step, model_id: id };
                  if (next?.protocol !== 'openai_chat' && next?.endpoint !== '/detect/grounding' && !requiresPrompt) {
                    nextStep.prompt = '';
                  }
                  if (next?.protocol === 'jev_decide') nextStep.attribute_prompt = '';
                  onUpdateStep(nextStep);
                }}
                options={modelCandidates.map(row => ({
                  value: row.id,
                  label: row.name + (isModelOffline(row) ? ' · 离线' : ''),
                  disabled: isModelOffline(row),
                }))}
              />
            </div>

            {(isDetection || isReview) && (
              <div>
                <Typography.Text>{isReview ? '审核类别' : '检测类别'}（用逗号或顿号分隔）</Typography.Text>
                <Input
                  aria-label="检测或审核类别"
                  style={{ marginTop: 4 }}
                  disabled={disabled}
                  value={(step.labels || []).join('、')}
                  placeholder={
                    isReview
                      ? '留空检查全部项目类别'
                      : currentModel?.labels?.length
                        ? `默认 ${currentModel.labels.join('、')}`
                        : '留空使用模型默认类别'
                  }
                  onChange={e =>
                    onUpdateStep({
                      ...step,
                      labels: e.target.value
                        .split(/[,，、;；\n]+/)
                        .map(s => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </div>
            )}

            <Space wrap size="middle">
              {isDetection && (
                <div>
                  <Typography.Text style={{ display: 'block', fontSize: 12 }}>检测置信度</Typography.Text>
                  <InputNumber
                    aria-label="检测置信度"
                    min={0}
                    max={1}
                    step={0.05}
                    disabled={disabled}
                    value={step.confidence ?? DEFAULT_DETECTION_CONFIDENCE}
                    onChange={v => onUpdateStep({ ...step, confidence: v ?? DEFAULT_DETECTION_CONFIDENCE })}
                  />
                </div>
              )}
              {isReview && (
                <div>
                  <Typography.Text style={{ display: 'block', fontSize: 12 }}>自动通过置信度</Typography.Text>
                  <InputNumber
                    aria-label="模型框自动通过置信度"
                    min={0}
                    max={1}
                    step={0.05}
                    disabled={disabled}
                    value={step.auto_approve_confidence ?? 0.9}
                    onChange={v => onUpdateStep({ ...step, auto_approve_confidence: v ?? 0.9 })}
                  />
                </div>
              )}
              {op === 'crop' && (
                <>
                  <div>
                    <Typography.Text style={{ display: 'block', fontSize: 12 }}>每侧外扩比例</Typography.Text>
                    <InputNumber
                      aria-label="每侧外扩比例"
                      min={0}
                      max={1}
                      step={0.05}
                      disabled={disabled}
                      value={step.expansion ?? 0.2}
                      onChange={v => onUpdateStep({ ...step, expansion: v ?? 0.2 })}
                    />
                  </div>
                  <div>
                    <Typography.Text style={{ display: 'block', fontSize: 12 }}>裁剪图最小长边</Typography.Text>
                    <InputNumber
                      aria-label="裁剪图最小长边"
                      min={0}
                      max={32768}
                      precision={0}
                      suffix="px"
                      disabled={disabled}
                      value={step.min_crop_long_edge ?? 0}
                      onChange={v => onUpdateStep({ ...step, min_crop_long_edge: v ?? 0 })}
                    />
                  </div>
                </>
              )}
            </Space>

            {op === 'crop' && (
              <Checkbox
                checked={!!step.save_input_boxes}
                disabled={disabled}
                onChange={e => onUpdateStep({ ...step, save_input_boxes: e.target.checked })}
              >
                将检测框同时映射保存到切图前的原图（状态为待审核，已有框保留）
              </Checkbox>
            )}

            {isQuality && (
              <Alert
                type="warning"
                showIcon
                title="合格图片进入下游；明确判为不合格的图片及其标注会从本项目删除（模型调用报错时保留原图）。"
              />
            )}

            {op === 'attributes' && (
              <>
                <div>
                  <Typography.Text style={{ display: 'block', marginBottom: 4 }}>属性模板应用方式</Typography.Text>
                  <Select
                    aria-label="属性模板应用方式"
                    value={categoryMode ? 'category' : 'single'}
                    style={{ width: 240 }}
                    disabled={disabled}
                    options={[
                      { value: 'single', label: '统一使用一套属性' },
                      { value: 'category', label: '按裁剪类别自动匹配模板' },
                    ]}
                    onChange={val =>
                      onUpdateStep({
                        ...step,
                        attribute_templates:
                          val === 'category'
                            ? jev
                              ? []
                              : defaults.filter(t => t.id.startsWith('builtin_'))
                            : undefined,
                      })
                    }
                  />
                </div>
                {categoryMode ? (
                  <div>
                    <Typography.Text style={{ display: 'block', marginBottom: 4 }}>选择类别属性模板</Typography.Text>
                    <Select
                      mode="multiple"
                      aria-label="类别属性模板"
                      style={{ width: '100%' }}
                      disabled={disabled}
                      value={(selectedTemplates || []).map(t => t.id)}
                      onChange={(ids: string[]) =>
                        onUpdateStep({
                          ...step,
                          attribute_templates: ids.flatMap(id => {
                            const found = availableTemplates.get(id);
                            return found ? [found] : [];
                          }),
                        })
                      }
                      options={Array.from(availableTemplates.values(), t => ({
                        value: t.id,
                        label: `${t.name} · ${attributeTargets(t.config).join('、')}`,
                        disabled: jev && t.config.fields.some(f => !f.options.length),
                      }))}
                    />
                    <div style={{ marginTop: 8 }}>{attributeAction?.(true)}</div>
                  </div>
                ) : (
                  <Space wrap>
                    {attributeAction?.(false)}
                    <Typography.Text type="secondary">
                      {attributes
                        ? `${attributeTargets(attributes).join('、')} · ${attributes.fields.length} 项${attributeLabel}属性`
                        : '尚未配置目标与属性'}
                    </Typography.Text>
                  </Space>
                )}

                {currentModel?.protocol === 'openai_chat' && (
                  <div>
                    <Typography.Text style={{ display: 'block', marginBottom: 4 }}>属性核心提示词</Typography.Text>
                    <PromptEditor
                      title="属性核心提示词"
                      buttonLabel="编辑属性核心提示词"
                      disabled={disabled || attributeDefault === undefined}
                      defaultValue={attributeDefault}
                      prefillDefault
                      value={step.attribute_prompt || ''}
                      onChange={val => onUpdateStep({ ...step, attribute_prompt: val })}
                      help="可编辑本步骤使用的核心指令。系统会在推理时追加属性定义与 JSON 输出格式。"
                    />
                  </div>
                )}
              </>
            )}

            {hasPrompt && (
              <div>
                <Typography.Text style={{ display: 'block', marginBottom: 4 }}>
                  {promptTitle} {requiresPrompt ? '*' : ''}
                </Typography.Text>
                <PromptEditor
                  title={promptTitle}
                  buttonLabel={`编辑${promptTitle}`}
                  required={requiresPrompt}
                  maxLength={requiresPrompt ? 2000 : 8000}
                  disabled={disabled || (op === 'caption' && !!currentModel && currentModel.protocol !== 'openai_chat')}
                  defaultValue={op === 'caption' ? currentModel?.chat_prompts?.caption : undefined}
                  referencePrompt={
                    isQuality
                      ? qualityDefault
                      : isReview
                        ? currentModel?.chat_prompts?.review
                        : op === 'attributes'
                          ? step.attribute_prompt || attributeDefault
                          : isDetection
                            ? currentModel?.labels?.join('；')
                            : undefined
                  }
                  referenceLabel={
                    isQuality
                      ? '系统质量判断提示词'
                      : isReview
                        ? '模型默认审核提示词'
                        : op === 'attributes'
                          ? '属性核心提示词'
                          : isDetection
                            ? '模型默认检测类别'
                            : undefined
                  }
                  value={step.prompt || ''}
                  onChange={val => onUpdateStep({ ...step, prompt: val })}
                  help={
                    isQuality
                      ? '填写合格条件，例如主体完整、清晰，无严重遮挡。'
                      : op === 'filter'
                        ? '填写命中保留条件，例如只保留人物正在吸烟的画面。'
                        : '选填补充指令。'
                  }
                />
              </div>
            )}
          </Space>
        )}

        {/* === 2. 条件分流节点 (branch) === */}
        {op === 'branch' && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              title="配置多条分流条件。未命中任何条件的图片会自动进入「默认 / 其他」分支，可在画布上点击对应分支「+」添加下游处理。"
            />
            <Checkbox
              checked={step.branch_config?.first_match ?? true}
              disabled={disabled}
              onChange={e =>
                onUpdateStep({
                  ...step,
                  branch_config: {
                    first_match: e.target.checked,
                    branches: step.branch_config?.branches || [],
                  },
                })
              }
            >
              仅走首个命中的分支（取消勾选时允许同一张图同时进入多个命中分支）
            </Checkbox>

            {(step.branch_config?.branches || []).map((b, idx) => (
              <Card
                key={b.key}
                size="small"
                title={`分支 ${idx + 1} · ${b.name}`}
                extra={
                  (step.branch_config?.branches.length || 0) > 1 && (
                    <Button
                      size="small"
                      type="text"
                      danger
                      disabled={disabled}
                      onClick={() => {
                        const nextList = (step.branch_config?.branches || []).filter((_, i) => i !== idx);
                        onUpdateStep({
                          ...step,
                          branch_config: { first_match: step.branch_config?.first_match ?? true, branches: nextList },
                        });
                      }}
                    >
                      删除分支
                    </Button>
                  )
                }
              >
                <Space orientation="vertical" size="small" style={{ width: '100%' }}>
                  <Space wrap>
                    <Input
                      addonBefore="分支名称"
                      value={b.name}
                      disabled={disabled}
                      style={{ width: 230 }}
                      onChange={e => updateBranchCase(idx, { name: e.target.value })}
                    />
                    <Select
                      value={b.match_mode || 'all'}
                      disabled={disabled}
                      style={{ width: 170 }}
                      onChange={v => updateBranchCase(idx, { match_mode: v })}
                      options={[
                        { value: 'all', label: '满足以下全部条件' },
                        { value: 'any', label: '满足任一条件即可' },
                      ]}
                    />
                  </Space>
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>裁剪图来源类别（逗号分隔，如 person, 人）</Typography.Text>
                    <Input
                      value={(b.crop_labels || []).join(', ')}
                      disabled={disabled}
                      placeholder="例如：person, 人"
                      onChange={e =>
                        updateBranchCase(idx, {
                          crop_labels: e.target.value.split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean),
                        })
                      }
                    />
                  </div>
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>包含检测框类别（逗号分隔）</Typography.Text>
                    <Input
                      value={(b.box_labels || []).join(', ')}
                      disabled={disabled}
                      placeholder="例如：fire, smoke"
                      onChange={e =>
                        updateBranchCase(idx, {
                          box_labels: e.target.value.split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean),
                        })
                      }
                    />
                  </div>
                  <Space wrap>
                    <InputNumber
                      addonBefore="最少框数"
                      min={0}
                      max={5000}
                      disabled={disabled}
                      value={b.min_boxes ?? null}
                      onChange={v => updateBranchCase(idx, { min_boxes: v })}
                    />
                    <InputNumber
                      addonBefore="最多框数"
                      min={0}
                      max={5000}
                      disabled={disabled}
                      value={b.max_boxes ?? null}
                      onChange={v => updateBranchCase(idx, { max_boxes: v })}
                    />
                  </Space>
                  <Space wrap>
                    <Input
                      addonBefore="属性字段 key"
                      placeholder="如 helmet"
                      style={{ width: 210 }}
                      disabled={disabled}
                      value={b.attribute_key || ''}
                      onChange={e => updateBranchCase(idx, { attribute_key: e.target.value })}
                    />
                    <Input
                      addonBefore="属性值命中"
                      placeholder="如 未佩戴, 无"
                      style={{ width: 240 }}
                      disabled={disabled}
                      value={(b.attribute_values || []).join(', ')}
                      onChange={e =>
                        updateBranchCase(idx, {
                          attribute_values: e.target.value.split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean),
                        })
                      }
                    />
                  </Space>
                </Space>
              </Card>
            ))}

            <Button
              icon={<PlusOutlined />}
              disabled={disabled || (step.branch_config?.branches.length || 0) >= 12}
              onClick={() => {
                const list = step.branch_config?.branches || [];
                let n = list.length + 1;
                while (list.some(item => item.key === `branch_${n}`)) n += 1;
                onUpdateStep({
                  ...step,
                  branch_config: {
                    first_match: step.branch_config?.first_match ?? true,
                    branches: [...list, { key: `branch_${n}`, name: `分支 ${n}`, match_mode: 'all', crop_labels: ['object'] }],
                  },
                });
              }}
            >
              添加条件分支
            </Button>
          </Space>
        )}

        {/* === 3. 图像基础规则过滤 (img_filter) === */}
        {op === 'img_filter' && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Alert type="info" showIcon title="纯本地像素级规则极速筛图，不调用远端大模型。至少填写一项阈值条件。" />
            <Space wrap>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小短边 (px)</Typography.Text>
                <InputNumber
                  min={1} max={32768} precision={0} disabled={disabled}
                  value={step.img_filter_config?.min_short_edge ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_short_edge: v } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小长边 (px)</Typography.Text>
                <InputNumber
                  min={1} max={32768} precision={0} disabled={disabled}
                  value={step.img_filter_config?.min_long_edge ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_long_edge: v } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小宽度 (px)</Typography.Text>
                <InputNumber
                  min={1} max={32768} precision={0} disabled={disabled}
                  value={step.img_filter_config?.min_width ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_width: v } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小高度 (px)</Typography.Text>
                <InputNumber
                  min={1} max={32768} precision={0} disabled={disabled}
                  value={step.img_filter_config?.min_height ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_height: v } })}
                />
              </div>
            </Space>
            <Space wrap>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最低清晰度（拉普拉斯方差，推荐 50~120）</Typography.Text>
                <InputNumber
                  min={0} max={100000} step={5} style={{ width: 220 }} disabled={disabled}
                  value={step.img_filter_config?.min_sharpness ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_sharpness: v } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>pHash 近重复汉明距离（推荐 4~6，留空不去重）</Typography.Text>
                <InputNumber
                  min={0} max={16} precision={0} style={{ width: 240 }} disabled={disabled}
                  value={step.img_filter_config?.dedup_phash_distance ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, dedup_phash_distance: v } })}
                />
              </div>
            </Space>
            <Space wrap>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最低平均亮度（0~255，防黑屏推荐 15）</Typography.Text>
                <InputNumber
                  min={0} max={255} style={{ width: 220 }} disabled={disabled}
                  value={step.img_filter_config?.min_brightness ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_brightness: v } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最高平均亮度（0~255，防过曝推荐 245）</Typography.Text>
                <InputNumber
                  min={0} max={255} style={{ width: 220 }} disabled={disabled}
                  value={step.img_filter_config?.max_brightness ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, max_brightness: v } })}
                />
              </div>
            </Space>
            <Space wrap>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小宽高比 (宽/高)</Typography.Text>
                <InputNumber
                  min={0.05} max={50} step={0.1} disabled={disabled}
                  value={step.img_filter_config?.min_aspect_ratio ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, min_aspect_ratio: v } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最大宽高比 (宽/高)</Typography.Text>
                <InputNumber
                  min={0.05} max={50} step={0.1} disabled={disabled}
                  value={step.img_filter_config?.max_aspect_ratio ?? null}
                  onChange={v => onUpdateStep({ ...step, img_filter_config: { ...step.img_filter_config, max_aspect_ratio: v } })}
                />
              </div>
            </Space>
          </Space>
        )}

        {/* === 4. 检测框规则清洗 (box_clean) === */}
        {op === 'box_clean' && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Alert type="info" showIcon title="对图片已有的候选检测框进行几何过滤、重命名与 NMS 去重（人工标注或已审核通过的框会自动受保护不覆盖）。" />
            <Space wrap>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小框宽 (px)</Typography.Text>
                <InputNumber
                  min={0} max={32768} disabled={disabled}
                  value={step.box_clean_config?.min_box_width ?? 0}
                  onChange={v => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, min_box_width: v ?? 0 } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小框高 (px)</Typography.Text>
                <InputNumber
                  min={0} max={32768} disabled={disabled}
                  value={step.box_clean_config?.min_box_height ?? 0}
                  onChange={v => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, min_box_height: v ?? 0 } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>四边截断容差 (px)</Typography.Text>
                <InputNumber
                  min={0} max={2000} disabled={disabled}
                  value={step.box_clean_config?.edge_margin_px ?? 0}
                  onChange={v => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, edge_margin_px: v ?? 0 } })}
                />
              </div>
            </Space>
            <Space wrap>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最小面积占比 (0~1)</Typography.Text>
                <InputNumber
                  min={0} max={0.99} step={0.005} disabled={disabled}
                  value={step.box_clean_config?.min_area_ratio ?? 0}
                  onChange={v => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, min_area_ratio: v ?? 0 } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>最大面积占比 (0~1)</Typography.Text>
                <InputNumber
                  min={0.01} max={1} step={0.05} disabled={disabled}
                  value={step.box_clean_config?.max_area_ratio ?? 1}
                  onChange={v => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, max_area_ratio: v ?? 1 } })}
                />
              </div>
              <div>
                <Typography.Text style={{ display: 'block', fontSize: 12 }}>NMS 去重 IoU 阈值</Typography.Text>
                <InputNumber
                  min={0.05} max={0.95} step={0.05} placeholder="留空不去重" disabled={disabled}
                  value={step.box_clean_config?.nms_iou_threshold ?? null}
                  onChange={v => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, nms_iou_threshold: v } })}
                />
              </div>
            </Space>
            <div>
              <Typography.Text style={{ fontSize: 12 }}>仅保留类别（留空保留全部）</Typography.Text>
              <Input
                value={(step.box_clean_config?.keep_labels || []).join(', ')}
                disabled={disabled}
                placeholder="例如：person, car"
                onChange={e =>
                  onUpdateStep({
                    ...step,
                    box_clean_config: {
                      ...step.box_clean_config,
                      keep_labels: e.target.value.split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean),
                    },
                  })
                }
              />
            </div>
            <div>
              <Typography.Text style={{ fontSize: 12 }}>剔除指定类别</Typography.Text>
              <Input
                value={(step.box_clean_config?.drop_labels || []).join(', ')}
                disabled={disabled}
                placeholder="例如：background, ignore"
                onChange={e =>
                  onUpdateStep({
                    ...step,
                    box_clean_config: {
                      ...step.box_clean_config,
                      drop_labels: e.target.value.split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean),
                    },
                  })
                }
              />
            </div>
            <div>
              <Typography.Text style={{ fontSize: 12 }}>类别重命名映射（JSON，如 {`{"ebike": "bicycle"}`}）</Typography.Text>
              <JsonInput
                aria-label="框类别重命名映射"
                value={renameText}
                disabled={disabled}
                autoSize={{ minRows: 2, maxRows: 5 }}
                onChange={txt => {
                  setRenameText(txt);
                  try {
                    const parsed = JSON.parse(txt || '{}');
                    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                      setRenameErr('');
                      onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, rename_map: parsed } });
                    } else {
                      setRenameErr('需为键值对 JSON 对象');
                    }
                  } catch {
                    setRenameErr('JSON 格式尚未闭合');
                  }
                }}
              />
              {renameErr && <Typography.Text type="warning" style={{ fontSize: 12 }}>{renameErr}</Typography.Text>}
            </div>
            <Space wrap>
              <Checkbox
                checked={step.box_clean_config?.save_boxes ?? true}
                disabled={disabled}
                onChange={e => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, save_boxes: e.target.checked } })}
              >
                保存清洗后的检测框为待审核版本
              </Checkbox>
              <Checkbox
                checked={step.box_clean_config?.pass_when_empty ?? false}
                disabled={disabled}
                onChange={e => onUpdateStep({ ...step, box_clean_config: { ...step.box_clean_config, pass_when_empty: e.target.checked } })}
              >
                清洗后无目标框时仍传递给下游
              </Checkbox>
            </Space>
          </Space>
        )}

        {/* === 5. 图像变换与脱敏 (img_transform) === */}
        {op === 'img_transform' && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Alert type="info" showIcon title="生成新的项目衍生图传递给下游，绝不修改共享原图。" />
            <div>
              <Typography.Text style={{ display: 'block', marginBottom: 4 }}>固定区域 ROI 裁剪（去除监控水印 / 边框）</Typography.Text>
              <Space wrap>
                <Select
                  value={step.img_transform_config?.roi_mode || 'none'}
                  disabled={disabled}
                  style={{ width: 200 }}
                  onChange={mode =>
                    onUpdateStep({
                      ...step,
                      img_transform_config: {
                        roi_mode: mode,
                        roi_ratio: mode === 'ratio' ? step.img_transform_config?.roi_ratio || [0, 0.08, 1, 1] : undefined,
                        roi_margins_px: mode === 'pixels' ? step.img_transform_config?.roi_margins_px || [0, 48, 0, 0] : undefined,
                        blur_labels: step.img_transform_config?.blur_labels || [],
                        blur_all_boxes: step.img_transform_config?.blur_all_boxes ?? false,
                        blur_radius: step.img_transform_config?.blur_radius ?? 18,
                      },
                    })
                  }
                  options={[
                    { value: 'none', label: '不裁剪区域' },
                    { value: 'ratio', label: '按比例保留区域 (0~1)' },
                    { value: 'pixels', label: '按四边裁去像素 (px)' },
                  ]}
                />
                <Button
                  size="small"
                  disabled={disabled}
                  onClick={() =>
                    onUpdateStep({
                      ...step,
                      img_transform_config: { ...(step.img_transform_config || { blur_radius: 18 }), roi_mode: 'ratio', roi_ratio: [0, 0.08, 1, 1] },
                    })
                  }
                >
                  预设：裁掉顶部 8% 水印
                </Button>
                <Button
                  size="small"
                  disabled={disabled}
                  onClick={() =>
                    onUpdateStep({
                      ...step,
                      img_transform_config: { ...(step.img_transform_config || { blur_radius: 18 }), roi_mode: 'ratio', roi_ratio: [0, 0, 1, 0.92] },
                    })
                  }
                >
                  预设：裁掉底部 8% 水印
                </Button>
              </Space>
            </div>

            {step.img_transform_config?.roi_mode === 'ratio' && (
              <Space wrap>
                {(['左 left', '上 top', '右 right', '下 bottom'] as const).map((lbl, i) => {
                  const arr: [number, number, number, number] = [...(step.img_transform_config?.roi_ratio || [0, 0, 1, 1])] as [number, number, number, number];
                  return (
                    <InputNumber
                      key={lbl}
                      addonBefore={lbl}
                      min={0} max={1} step={0.02} style={{ width: 155 }} disabled={disabled}
                      value={arr[i]}
                      onChange={v => {
                        arr[i] = Number(v ?? (i < 2 ? 0 : 1));
                        onUpdateStep({ ...step, img_transform_config: { ...step.img_transform_config!, roi_ratio: arr } });
                      }}
                    />
                  );
                })}
              </Space>
            )}

            {step.img_transform_config?.roi_mode === 'pixels' && (
              <Space wrap>
                {(['左裁 px', '上裁 px', '右裁 px', '下裁 px'] as const).map((lbl, i) => {
                  const arr: [number, number, number, number] = [...(step.img_transform_config?.roi_margins_px || [0, 0, 0, 0])] as [number, number, number, number];
                  return (
                    <InputNumber
                      key={lbl}
                      addonBefore={lbl}
                      min={0} max={8192} precision={0} style={{ width: 155 }} disabled={disabled}
                      value={arr[i]}
                      onChange={v => {
                        arr[i] = Number(v ?? 0);
                        onUpdateStep({ ...step, img_transform_config: { ...step.img_transform_config!, roi_margins_px: arr } });
                      }}
                    />
                  );
                })}
              </Space>
            )}

            <Divider style={{ margin: '4px 0' }} />
            <div>
              <Typography.Text strong>目标框高斯模糊打码脱敏</Typography.Text>
              <div style={{ marginTop: 6 }}>
                <Checkbox
                  checked={!!step.img_transform_config?.blur_all_boxes}
                  disabled={disabled}
                  onChange={e =>
                    onUpdateStep({
                      ...step,
                      img_transform_config: { ...(step.img_transform_config || { roi_mode: 'none' }), blur_all_boxes: e.target.checked },
                    })
                  }
                >
                  对图片全部已有检测框区域模糊打码
                </Checkbox>
              </div>
              {!step.img_transform_config?.blur_all_boxes && (
                <Input
                  style={{ marginTop: 6 }}
                  disabled={disabled}
                  placeholder="填写需要模糊打码的检测框类别，逗号分隔（如 face, plate, person）"
                  value={(step.img_transform_config?.blur_labels || []).join(', ')}
                  onChange={e =>
                    onUpdateStep({
                      ...step,
                      img_transform_config: {
                        ...(step.img_transform_config || { roi_mode: 'none' }),
                        blur_labels: e.target.value.split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean),
                      },
                    })
                  }
                />
              )}
              <div style={{ marginTop: 8 }}>
                <InputNumber
                  addonBefore="模糊强度半径"
                  min={2} max={60} precision={0} disabled={disabled}
                  value={step.img_transform_config?.blur_radius ?? 18}
                  onChange={v =>
                    onUpdateStep({
                      ...step,
                      img_transform_config: { ...(step.img_transform_config || { roi_mode: 'none' }), blur_radius: v ?? 18 },
                    })
                  }
                />
              </div>
            </div>
          </Space>
        )}

        {/* === 6. Python 安全沙箱脚本节点 (script) === */}
        {op === 'script' && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Card size="small" style={{ background: 'rgba(182, 156, 255, 0.06)', borderColor: 'rgba(182, 156, 255, 0.3)' }}>
              <Space orientation="vertical" size="small" style={{ width: '100%' }}>
                <Space>
                  <ThunderboltOutlined style={{ color: '#c8b5ff' }} />
                  <Typography.Text strong>AI 一句话自动写脚本</Typography.Text>
                  <Tag color="purple">自动生成并通过沙箱安全校验</Tag>
                </Space>
                <Space.Compact style={{ width: '100%' }}>
                  <Input
                    aria-label="AI 脚本需求描述"
                    value={aiPrompt}
                    disabled={disabled || aiBusy}
                    placeholder="例如：去掉面积小于图片 2% 的小框，如果剩余框里有 person 则通过…"
                    onChange={e => setAiPrompt(e.target.value)}
                    onPressEnter={e => { e.preventDefault(); void generateCodeWithAi(); }}
                  />
                  <Button type="primary" loading={aiBusy} disabled={disabled || !aiPrompt.trim()} onClick={() => void generateCodeWithAi()}>
                    AI 写代码
                  </Button>
                </Space.Compact>
                {aiError && <Alert type="error" showIcon title={aiError} />}
                {aiNote && <Alert type="success" showIcon title={aiNote} />}
              </Space>
            </Card>

            <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
              <Typography.Text strong>Python 代码（入口函数 `def process(ctx):`）</Typography.Text>
              <Select<number>
                size="small"
                placeholder="载入常用示例脚本…"
                style={{ width: 220 }}
                disabled={disabled}
                value={undefined}
                options={SCRIPT_SNIPPETS.map((sn, i) => ({ value: i, label: sn.label }))}
                onSelect={idx => {
                  if (idx === undefined) return;
                  onUpdateStep({
                    ...step,
                    script_config: { ...(step.script_config || { timeout_seconds: 5 }), code: SCRIPT_SNIPPETS[idx].code },
                  });
                }}
              />
            </Space>

            <Input.TextArea
              aria-label="Python 处理脚本代码"
              className="wf-code-editor"
              disabled={disabled}
              value={step.script_config?.code ?? DEFAULT_PYTHON_SCRIPT}
              autoSize={{ minRows: 12, maxRows: 22 }}
              spellCheck={false}
              onChange={e =>
                onUpdateStep({
                  ...step,
                  script_config: { ...(step.script_config || { timeout_seconds: 5 }), code: e.target.value },
                })
              }
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              沙箱环境：禁网、隔离环境变量与宿主机文件，支持 `PIL` (`Image`, `ImageFilter`, `ImageStat`, `ImageOps`, `ImageDraw`)、`imagehash`、`math`、`json`、`re`、`statistics`、`collections`、`itertools`。
            </Typography.Text>

            <Space wrap>
              <InputNumber
                addonBefore="单图超时"
                suffix="秒"
                min={0.5}
                max={15}
                step={0.5}
                style={{ width: 170 }}
                disabled={disabled}
                value={step.script_config?.timeout_seconds ?? 5}
                onChange={v =>
                  onUpdateStep({
                    ...step,
                    script_config: { ...(step.script_config || { code: DEFAULT_PYTHON_SCRIPT }), timeout_seconds: v ?? 5 },
                  })
                }
              />
              <Checkbox
                checked={step.script_config?.save_boxes ?? true}
                disabled={disabled}
                onChange={e =>
                  onUpdateStep({
                    ...step,
                    script_config: { ...(step.script_config || { code: DEFAULT_PYTHON_SCRIPT }), save_boxes: e.target.checked },
                  })
                }
              >
                写回返回的 boxes 检测框
              </Checkbox>
              <Checkbox
                checked={step.script_config?.save_attributes ?? true}
                disabled={disabled}
                onChange={e =>
                  onUpdateStep({
                    ...step,
                    script_config: { ...(step.script_config || { code: DEFAULT_PYTHON_SCRIPT }), save_attributes: e.target.checked },
                  })
                }
              >
                写回 attributes/description
              </Checkbox>
            </Space>
          </Space>
        )}
      </Space>
    </Drawer>
  );
}
