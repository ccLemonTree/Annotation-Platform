import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Input, Select, Space, Tag, Typography } from 'antd';
import { ThunderboltOutlined, UndoOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import { getWorkflowModel, isModelOffline, type ModelService } from '../../api/modelServices';
import { aiGenerateWorkflow, type WorkflowAiGenerateResult, type WorkflowConfig } from '../../api/projectWork';

const QUICK_PROMPTS = [
  '先过滤过暗或模糊图片，再切出人员和车辆，按类别分流后分别进行属性分析',
  '对原图执行目标检测预标注，再用 Python 脚本过滤宽度小于 24px 或贴边的残缺框，最后交 AI 审核',
  '裁掉画面顶部 8% 的监控时间水印，并对识别到的人员或车牌区域高斯模糊打码脱敏',
  '使用免模型规则过滤短边小于 256px、清晰度低于 60 的图片，并按 pHash 去重',
];

const errorText = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail
    : 'AI 编排请求失败，请检查编排模型状态或稍后重试';

export default function WorkflowCopilotBar({
  projectId,
  models,
  currentWorkflow,
  disabled,
  canUndo,
  onApply,
  onUndo,
}: {
  projectId?: string;
  models: ModelService[];
  currentWorkflow: WorkflowConfig;
  disabled?: boolean;
  canUndo?: boolean;
  onApply: (result: WorkflowAiGenerateResult) => void;
  onUndo?: () => void;
}) {
  const llmOptions = useMemo(
    () => models.filter(m => m.enabled !== false && m.protocol === 'openai_chat' && ['llm', 'caption'].includes(m.kind)),
    [models],
  );
  const [selectedModelId, setSelectedModelId] = useState<string>();
  const [prompt, setPrompt] = useState('');
  const [refineExisting, setRefineExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [lastSummary, setLastSummary] = useState<{ summary: string; warnings: string[]; modelName: string }>();

  useEffect(() => {
    let active = true;
    getWorkflowModel()
      .then(savedId => {
        if (!active) return;
        if (savedId && llmOptions.some(m => m.id === savedId && !isModelOffline(m))) {
          setSelectedModelId(savedId);
        } else {
          const preferred =
            llmOptions.find(m => m.kind === 'llm' && !isModelOffline(m)) ||
            llmOptions.find(m => !isModelOffline(m));
          if (preferred) setSelectedModelId(preferred.id);
        }
      })
      .catch(() => {
        if (!active) return;
        const preferred = llmOptions.find(m => !isModelOffline(m));
        if (preferred) setSelectedModelId(preferred.id);
      });
    return () => { active = false; };
  }, [llmOptions]);

  const generate = async () => {
    const trimmed = prompt.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await aiGenerateWorkflow(projectId, {
        prompt: trimmed,
        model_id: selectedModelId || null,
        current_workflow: refineExisting ? currentWorkflow : undefined,
      });
      setLastSummary({ summary: res.summary, warnings: res.warnings || [], modelName: res.orchestrator_model_name });
      onApply(res);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="wf-copilot-bar" aria-label="AI 自动编排工作流">
      <div className="wf-copilot-header">
        <div className="wf-copilot-badge">
          <ThunderboltOutlined />
          <span>AI 自动编排工作流</span>
          <Tag color="blue" style={{ marginInlineStart: 4 }}>自然语言 → 流程图 + 自动脚本</Tag>
        </div>
        <Space wrap size="small">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>编排模型：</Typography.Text>
          {llmOptions.length > 0 ? (
            <Select
              size="small"
              aria-label="选择 AI 编排模型"
              style={{ width: 220 }}
              value={selectedModelId}
              disabled={disabled || busy}
              onChange={setSelectedModelId}
              options={llmOptions.map(m => ({
                value: m.id,
                disabled: isModelOffline(m),
                label: `${m.name}${m.kind === 'llm' ? ' · 文本/代码' : ' · 视觉对话'}${isModelOffline(m) ? '（离线）' : ''}`,
              }))}
            />
          ) : (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>
              未配置可用编排模型，<Link to={projectId ? `/models?project_id=${projectId}` : '/models'}>前往模型管理添加 →</Link>
            </Typography.Text>
          )}
          <Select
            size="small"
            aria-label="AI 编排模式"
            style={{ width: 148 }}
            value={refineExisting ? 'refine' : 'fresh'}
            disabled={disabled || busy}
            onChange={v => setRefineExisting(v === 'refine')}
            options={[
              { value: 'fresh', label: '全新生成整图' },
              { value: 'refine', label: '在当前图上修改' },
            ]}
          />
          {canUndo && onUndo && (
            <Button size="small" icon={<UndoOutlined />} disabled={disabled || busy} onClick={onUndo}>
              撤回 AI 修改
            </Button>
          )}
        </Space>
      </div>

      <Space.Compact style={{ width: '100%' }}>
        <Input.TextArea
          aria-label="AI 工作流编排需求描述"
          value={prompt}
          disabled={disabled || busy}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault();
              void generate();
            }
          }}
          placeholder="用大白话描述你的处理目标，例如：先过滤模糊和过暗的图片，切出行人和车辆后分流，行人分析衣着属性，车辆用脚本过滤小框…（Ctrl + Enter 生成）"
          autoSize={{ minRows: 2, maxRows: 4 }}
          style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
        />
        <Button
          type="primary"
          loading={busy}
          disabled={disabled || !prompt.trim() || !llmOptions.length}
          onClick={() => void generate()}
          style={{ height: 'auto', minWidth: 112 }}
        >
          {refineExisting ? 'AI 调整流程' : 'AI 生成流程'}
        </Button>
      </Space.Compact>

      <div className="wf-copilot-prompts">
        <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: '24px' }}>新手示例：</Typography.Text>
        {QUICK_PROMPTS.map(sample => (
          <button
            key={sample}
            type="button"
            className="wf-prompt-chip"
            disabled={disabled || busy}
            onClick={() => setPrompt(sample)}
          >
            {sample.length > 30 ? sample.slice(0, 30) + '…' : sample}
          </button>
        ))}
      </div>

      {error && <Alert type="error" showIcon title={error} closable onClose={() => setError('')} style={{ marginTop: 10 }} />}
      {lastSummary && !error && (
        <Alert
          type={lastSummary.warnings.length ? 'warning' : 'success'}
          showIcon
          closable
          onClose={() => setLastSummary(undefined)}
          style={{ marginTop: 10 }}
          title={`AI 编排完成（${lastSummary.modelName}）：${lastSummary.summary}`}
          description={
            lastSummary.warnings.length ? (
              <ul style={{ margin: '4px 0 0', paddingInlineStart: 18 }}>
                {lastSummary.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            ) : '点击下方画布中的任意节点可在右侧查看或微调参数与脚本；建议先点「样本试运行」确认效果。'
          }
        />
      )}
    </section>
  );
}
