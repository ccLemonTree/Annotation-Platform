import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Modal, Select, Space, Tag, Typography } from 'antd';
import { isAxiosError } from 'axios';
import { getAssets, type Asset } from '../../api/assets';
import {
  dryRunWorkflow,
  type WorkflowConfig,
  type WorkflowDryRunResult,
} from '../../api/projectWork';
import { labelColor } from '../detectionEditor';

const errorText = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail
    : '试运行失败，请核对各节点模型与规则配置后重试';

export default function WorkflowDryRunModal({
  open,
  projectId,
  workflow,
  imageKind,
  sourceJobId,
  onClose,
  onResult,
}: {
  open: boolean;
  projectId: string;
  workflow: WorkflowConfig;
  imageKind: 'originals' | 'crops' | 'all';
  sourceJobId?: string;
  onClose: () => void;
  onResult: (result: WorkflowDryRunResult) => void;
}) {
  const [candidates, setCandidates] = useState<Asset[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [sampleError, setSampleError] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [report, setReport] = useState<WorkflowDryRunResult>();
  const abortRef = useRef<AbortController | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const loadCandidates = useCallback(async (keyword?: string) => {
    setCandidatesLoading(true);
    setSampleError('');
    try {
      const res = await getAssets({
        project_id: projectId,
        image_kind: imageKind,
        work_job_id: sourceJobId,
        q: keyword?.trim() || undefined,
        page: 1,
        page_size: 30,
      });
      setCandidates(prev => {
        const chosen = prev.filter(item => selectedIds.includes(item.id) && !res.items.some(r => r.id === item.id));
        return [...chosen, ...res.items];
      });
    } catch {
      setSampleError('读取候选样本图片失败；可直接开始自动抽样试运行，或点击重试。');
    } finally {
      setCandidatesLoading(false);
    }
  }, [projectId, imageKind, sourceJobId, selectedIds]);

  useEffect(() => {
    setSelectedIds([]);
  }, [projectId, imageKind, sourceJobId]);

  useEffect(() => {
    if (!open) return;
    void loadCandidates();
    return () => {
      clearTimeout(searchTimer.current);
      abortRef.current?.abort();
    };
  }, [open, projectId, imageKind, sourceJobId]);

  const cancelRun = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const handleClose = () => {
    cancelRun();
    onClose();
  };

  const execute = async () => {
    cancelRun();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError('');
    try {
      const res = await dryRunWorkflow(
        projectId,
        {
          steps: workflow.steps,
          edges: workflow.edges,
          attribute_schema: workflow.attribute_schema,
          image_kind: imageKind,
          source_job_id: sourceJobId,
          sample_asset_ids: selectedIds.length ? selectedIds : undefined,
        },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setReport(res);
        onResult(res);
      }
    } catch (cause) {
      if (controller.signal.aborted || (isAxiosError(cause) && cause.code === 'ERR_CANCELED')) {
        setError('');
      } else {
        setError(errorText(cause));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      title="工作流样本试运行（仅内存预览 · 不落库）"
      width={920}
      onCancel={handleClose}
      styles={{ body: { maxHeight: '72vh', overflowY: 'auto' } }}
      footer={
        <Space>
          {busy ? (
            <Button danger onClick={cancelRun}>取消试运行</Button>
          ) : (
            <Button onClick={handleClose}>关闭</Button>
          )}
          <Button type="primary" loading={busy} onClick={() => void execute()}>
            开始试运行
          </Button>
        </Space>
      }
    >
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          title="在启动大批量任务前，抽取 1~3 张真实项目图片验证每个模型、规则和 Python 脚本的输入输出，不会新增裁剪图、修改标注或删除图片。"
        />
        <Space wrap style={{ width: '100%' }}>
          <Typography.Text>测试样本（可选，留空自动取最新 2 张）：</Typography.Text>
          <Select
            mode="multiple"
            maxCount={3}
            allowClear
            showSearch
            filterOption={false}
            loading={candidatesLoading}
            aria-label="选择试运行样本图片"
            placeholder="搜索文件名或留空自动按当前输入范围抽样 2 张"
            style={{ minWidth: 360, maxWidth: '100%' }}
            value={selectedIds}
            disabled={busy}
            onSearch={val => {
              clearTimeout(searchTimer.current);
              searchTimer.current = setTimeout(() => { void loadCandidates(val); }, 250);
            }}
            onClear={() => void loadCandidates()}
            onChange={setSelectedIds}
            options={candidates.map(a => ({
              value: a.id,
              label: `${a.original_name}（${a.width}×${a.height}${a.source_type === 'model_crop' ? ' · 裁剪图' : ''}）`,
            }))}
          />
        </Space>

        {sampleError && (
          <Alert
            type="warning"
            showIcon
            title={sampleError}
            action={<Button size="small" disabled={candidatesLoading} onClick={() => void loadCandidates()}>重试读取样本</Button>}
          />
        )}

        {error && <Alert type="error" showIcon title={error} />}

        {!report && !error && !busy && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点击右下角「开始试运行」查看每个节点的执行结果与预览图" />
        )}

        {report && (
          <Space orientation="vertical" size="small" style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              本次抽样 {report.sample_count} 张：{report.samples.map(s => `${s.original_name}(${s.width}×${s.height})`).join('、')}
            </Typography.Text>
            {report.nodes.map((node, idx) => (
              <Card
                key={node.node_key}
                size="small"
                title={
                  <Space wrap>
                    <Tag>{idx + 1}</Tag>
                    <span>{node.title}</span>
                    <Tag color={node.error ? 'error' : 'success'}>
                      输入 {node.input_count} → 输出 {node.output_count} 张
                    </Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{node.duration_ms} ms</Typography.Text>
                  </Space>
                }
              >
                {node.error && <Alert type="error" showIcon title={`节点执行报错：${node.error}`} style={{ marginBottom: 8 }} />}
                {!node.items.length && !node.error && (
                  <Typography.Text type="secondary">上游没有满足条件的图片流入此节点。</Typography.Text>
                )}
                <Space orientation="vertical" size="small" style={{ width: '100%' }}>
                  {node.items.map((item, itemIdx) => (
                    <div
                      key={itemIdx}
                      style={{
                        padding: '8px 10px',
                        borderRadius: 6,
                        border: '1px solid var(--ui-border)',
                        background: 'var(--ui-background)',
                      }}
                    >
                      <Space wrap>
                        <Tag color={item.passed ? 'success' : 'default'}>{item.passed ? '通过 / 命中' : '拦截 / 跳过'}</Tag>
                        <Typography.Text strong>{item.name}</Typography.Text>
                        <Typography.Text type="secondary">{item.reason}</Typography.Text>
                      </Space>
                      {!!item.logs?.length && (
                        <pre style={{ margin: '6px 0 0', padding: 8, background: 'var(--ui-code-bg, var(--ui-background))', border: '1px solid var(--ui-border)', borderRadius: 4, fontSize: 12, overflowX: 'auto' }}>
                          {item.logs.join('\n')}
                        </pre>
                      )}
                      {item.preview_url && !!item.boxes?.length && item.width && item.height && (
                        <div style={{ position: 'relative', display: 'inline-block', marginTop: 8, maxWidth: 260, lineHeight: 0 }}>
                          <img src={item.preview_url} alt={item.name} style={{ width: '100%', borderRadius: 4 }} />
                          <svg
                            viewBox={`0 0 ${item.width} ${item.height}`}
                            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                          >
                            {item.boxes.map((b, bi) => {
                              const color = labelColor(b.label);
                              const fontSize = Math.max(14, item.width! / 38);
                              return (
                                <g key={bi}>
                                  <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="none" stroke="#0b101b" strokeWidth={6} vectorEffect="non-scaling-stroke" />
                                  <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="none" stroke="white" strokeWidth={4} vectorEffect="non-scaling-stroke" />
                                  <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
                                  <text
                                    x={b.x}
                                    y={Math.max(fontSize, b.y - 4)}
                                    fill="white"
                                    stroke="#0b101b"
                                    strokeWidth={Math.max(3, fontSize * 0.2)}
                                    paintOrder="stroke"
                                    strokeLinejoin="round"
                                    fontSize={fontSize}
                                  >
                                    {b.label}{typeof b.score === 'number' ? ` ${(b.score * 100).toFixed(1)}%` : ''}
                                  </text>
                                </g>
                              );
                            })}
                          </svg>
                        </div>
                      )}
                      {!!item.derived_previews?.length && (
                        <Space wrap style={{ marginTop: 8 }}>
                          {item.derived_previews.map((src, pi) => (
                            <img
                              key={pi}
                              src={src}
                              alt={`衍生预览 ${pi + 1}`}
                              style={{ height: 84, borderRadius: 4, border: '1px solid var(--ui-border)' }}
                            />
                          ))}
                        </Space>
                      )}
                    </div>
                  ))}
                </Space>
              </Card>
            ))}
          </Space>
        )}
      </Space>
    </Modal>
  );
}
