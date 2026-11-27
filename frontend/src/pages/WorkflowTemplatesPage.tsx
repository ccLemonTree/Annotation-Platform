import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Card, Input, Modal, Popconfirm, Space, Table, Tag } from 'antd';
import { isAxiosError } from 'axios';
import { getModelServices, getPromptDefaults, isModelOffline, type ModelService } from '../api/modelServices';
import type { AttributeSchema } from '../api/objectAttributes';
import {
  deleteWorkflowTemplate,
  getWorkflowTemplates,
  saveWorkflowTemplate,
  type WorkflowEdge,
  type WorkflowStep,
  type WorkflowTemplate,
} from '../api/projectWork';
import { AttributeFields, validAttributeFields } from '../components/ObjectAttributes';
import { operations } from '../components/WorkflowStepsFields';
import WorkflowGraphBuilder from '../components/workflow/WorkflowGraphBuilder';
import { MODEL_OPERATIONS, createDefaultStep, normalizeGraph } from '../components/workflow/workflowGraph';

const errorText = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail
    : '操作失败，请重试';
const emptyAttributes: AttributeSchema = { target_label: [], fields: [] };

export default function WorkflowTemplatesPage() {
  const [rows, setRows] = useState<WorkflowTemplate[]>([]);
  const [editing, setEditing] = useState<WorkflowTemplate | null>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [removing, setRemoving] = useState(false);

  useEffect(() => {
    let active = true;
    getWorkflowTemplates()
      .then(data => { if (active) setRows(data); })
      .catch(cause => { if (active) setError(errorText(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const remove = async (id: string) => {
    setError('');
    setRemoving(true);
    try {
      await deleteWorkflowTemplate(id);
      setRows(previous => previous.filter(row => row.id !== id));
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setRemoving(false);
    }
  };

  if (editing !== undefined) {
    return (
      <TemplateEditor
        key={editing?.id || 'new'}
        template={editing}
        onCancel={() => setEditing(undefined)}
        onSaved={saved => {
          setRows(previous => [...previous.filter(row => row.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')));
          setEditing(undefined);
        }}
      />
    );
  }

  return (
    <Card
      title="工作流模板"
      extra={
        <Button type="primary" disabled={loading} onClick={() => { setError(''); setEditing(null); }}>
          新建模板
        </Button>
      }
    >
      {error && <Alert showIcon type="error" title={error} style={{ marginBottom: 16 }} />}
      <Table
        rowKey="id"
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: '暂无工作流模板' }}
        scroll={{ x: 640 }}
        columns={[
          { title: '模板名称', dataIndex: 'name', width: 240 },
          {
            title: '流程图摘要',
            render: (_, row) => (
              <Space wrap size={4}>
                <Tag color="blue">{row.steps.length} 个节点</Tag>
                <span>{row.steps.map(step => step.title || operations[step.operation]).join(' → ')}</span>
              </Space>
            ),
          },
          {
            title: '操作',
            width: 170,
            render: (_, row) => (
              <Space size="small">
                <Button type="link" size="small" onClick={() => setEditing(row)}>编辑</Button>
                <Popconfirm
                  title="删除这个模板？"
                  description="已经启动的工作流会继续执行。"
                  okText="删除模板"
                  cancelText="取消"
                  onConfirm={() => remove(row.id)}
                >
                  <Button type="link" size="small" danger disabled={removing}>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}

function TemplateEditor({
  template,
  onCancel,
  onSaved,
}: {
  template: WorkflowTemplate | null;
  onCancel: () => void;
  onSaved: (row: WorkflowTemplate) => void;
}) {
  const { message } = App.useApp();
  const initial = useMemo(() => {
    if (template) {
      return normalizeGraph(structuredClone(template.steps), template.edges ? structuredClone(template.edges) : undefined);
    }
    return normalizeGraph([createDefaultStep('crop', [])], []);
  }, [template]);

  const [name, setName] = useState(template?.name || '');
  const [steps, setSteps] = useState<WorkflowStep[]>(initial.steps);
  const [edges, setEdges] = useState<WorkflowEdge[]>(initial.edges);
  const [models, setModels] = useState<ModelService[]>([]);
  const [attributeDefault, setAttributeDefault] = useState<string>();
  const [qualityDefault, setQualityDefault] = useState<string>();
  const [attributes, setAttributes] = useState(template?.attribute_schema);
  const [attributeDraft, setAttributeDraft] = useState(emptyAttributes);
  const [attributeOpen, setAttributeOpen] = useState(false);
  const [cropLabels, setCropLabels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    Promise.all([getModelServices({ enabled: true }), getPromptDefaults()])
      .then(([services, defaults]) => {
        if (active) {
          setModels(services);
          setAttributeDefault(defaults.attributes);
          setQualityDefault(defaults.quality);
        }
      })
      .catch(cause => { if (active) setError(errorText(cause)); });
    return () => { active = false; };
  }, []);

  const submit = async () => {
    if (!name.trim()) {
      setError('请填写模板名称。');
      return;
    }
    const invalidModelIdx = steps.findIndex(
      step =>
        MODEL_OPERATIONS.has(step.operation) &&
        !models.some(m => m.id === step.model_id && m.enabled !== false && !isModelOffline(m)),
    );
    if (invalidModelIdx >= 0) {
      setError(`第 ${invalidModelIdx + 1} 步「${steps[invalidModelIdx].title || operations[steps[invalidModelIdx].operation]}」尚未选择可用在线模型。`);
      return;
    }
    const needsAttributes = steps.some(step => step.operation === 'attributes' && step.attribute_templates == null);
    if (needsAttributes && !attributes) {
      setError('请先配置目标与属性。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const saved = await saveWorkflowTemplate(
        {
          name: name.trim(),
          steps,
          edges,
          attribute_schema: needsAttributes ? attributes : undefined,
        },
        template?.id,
      );
      message.success('工作流模板已保存');
      onSaved(saved);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={template ? '编辑工作流模板' : '新建工作流模板'}>
      {error && <Alert showIcon type="error" title={error} style={{ marginBottom: 16 }} />}
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <div>
          <label htmlFor="tpl-name-input" style={{ display: 'block', marginBottom: 6, color: 'var(--ui-muted)' }}>
            * 模板名称
          </label>
          <Input
            id="tpl-name-input"
            aria-label="模板名称"
            style={{ maxWidth: 360 }}
            maxLength={100}
            disabled={busy}
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </div>

        <WorkflowGraphBuilder
          workflowName={name || '工作流模板'}
          steps={steps}
          edges={edges}
          models={models}
          inputLabel="载入项目时选择输入图片"
          attributeDefault={attributeDefault}
          qualityDefault={qualityDefault}
          attributes={attributes}
          attributeLabel="模板"
          disabled={busy}
          onChangeGraph={next => {
            if (next.name !== undefined && !name.trim()) setName(next.name);
            setSteps(next.steps);
            setEdges(next.edges);
          }}
          attributeAction={(idx, category) =>
            !category && (
              <Button
                onClick={() => {
                  const crop = steps.slice(0, idx).findLast(s => s.operation === 'crop');
                  const labels = crop?.labels?.length
                    ? crop.labels
                    : crop?.model_id
                      ? models.find(m => m.id === crop.model_id)?.labels || []
                      : [];
                  setCropLabels(labels);
                  setAttributeDraft(attributes ?? emptyAttributes);
                  setAttributeOpen(true);
                }}
              >
                配置目标与属性
              </Button>
            )
          }
        />

        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Space wrap>
            <Button disabled={busy} onClick={onCancel}>返回模板列表</Button>
            <Button type="primary" loading={busy} onClick={() => void submit()}>保存模板</Button>
          </Space>
        </div>
      </Space>

      <Modal
        open={attributeOpen}
        title="模板目标与属性"
        width={1040}
        onCancel={() => setAttributeOpen(false)}
        okText="应用属性"
        cancelText="取消"
        okButtonProps={{ disabled: !validAttributeFields(attributeDraft) }}
        onOk={() => { setAttributes(attributeDraft); setAttributeOpen(false); }}
      >
        <Space orientation="vertical" style={{ width: '100%' }}>
          <AttributeFields config={attributeDraft} setConfig={setAttributeDraft} targetLabels={cropLabels} />
        </Space>
      </Modal>
    </Card>
  );
}
