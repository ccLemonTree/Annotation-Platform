import { useState } from 'react';
import { Button, Dropdown, Segmented, Space, Tag, Tooltip, Typography } from 'antd';
import type { MenuProps } from 'antd';
import type { ModelService } from '../../api/modelServices';
import type { WorkflowDryRunNodeReport, WorkflowEdge, WorkflowOperation, WorkflowStep } from '../../api/projectWork';
import {
  CATEGORY_LABELS,
  NODE_MENU_GROUPS,
  WORKFLOW_OPERATIONS,
  computeGraphLayout,
  getNodeCategory,
  summarizeStep,
} from './workflowGraph';
import './workflowCanvas.css';

export interface InsertTarget {
  mode: 'between' | 'append' | 'entry';
  sourceKey: string;
  sourceHandle: string;
  targetKey?: string;
}

export default function WorkflowCanvas({
  steps,
  edges,
  models,
  inputLabel,
  selectedKey,
  dryRunByNode,
  readonly = false,
  onSelectNode,
  onInsertNode,
}: {
  steps: WorkflowStep[];
  edges: WorkflowEdge[];
  models: ModelService[];
  inputLabel: string;
  selectedKey?: string;
  dryRunByNode?: Map<string, WorkflowDryRunNodeReport>;
  readonly?: boolean;
  onSelectNode?: (nodeKey: string) => void;
  onInsertNode?: (target: InsertTarget, operation: WorkflowOperation) => void;
}) {
  const [zoom, setZoom] = useState<number>(1);
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const layout = computeGraphLayout(steps, edges, true);

  const buildInsertMenu = (target: InsertTarget): MenuProps => ({
    items: NODE_MENU_GROUPS.map(group => ({
      type: 'group' as const,
      key: group.group,
      label: group.group,
      children: group.items.map(item => ({
        key: item.operation,
        label: (
          <div style={{ maxWidth: 260 }}>
            <div style={{ fontWeight: 500 }}>{item.label}</div>
            <div style={{ fontSize: 11, color: 'var(--ui-muted)', whiteSpace: 'normal' }}>{item.hint}</div>
          </div>
        ),
        onClick: () => onInsertNode?.(target, item.operation),
      })),
    })),
  });

  const outgoingByHandle = new Map<string, WorkflowEdge[]>();
  for (const e of layout.edges) {
    const k = `${e.source}:${e.source_handle}`;
    if (!outgoingByHandle.has(k)) outgoingByHandle.set(k, []);
    outgoingByHandle.get(k)!.push(e);
  }

  return (
    <div className="wf-canvas-shell">
      <div className="wf-canvas-toolbar">
        <div className="wf-legend">
          <span><i className="wf-legend-dot" style={{ background: 'var(--ui-primary)' }} />模型推理</span>
          <span><i className="wf-legend-dot" style={{ background: 'var(--ui-success)' }} />内置功能</span>
          <span><i className="wf-legend-dot" style={{ background: 'var(--ui-warning)' }} />条件分流</span>
          <span><i className="wf-legend-dot" style={{ background: 'var(--ui-node-script-accent, #b69cff)' }} />Python 脚本</span>
          {!readonly && <span>· 点击节点编辑参数，点击连线「+」插入步骤或分支</span>}
        </div>
        <Space size="small">
          <Segmented
            size="small"
            aria-label="工作流展示视图"
            value={viewMode}
            onChange={v => setViewMode(v as 'graph' | 'list')}
            options={[
              { value: 'graph', label: '流程图' },
              { value: 'list', label: '步骤列表' },
            ]}
          />
          {viewMode === 'graph' && (
            <Segmented
              size="small"
              aria-label="画布缩放比例"
              value={zoom}
              onChange={v => setZoom(Number(v))}
              options={[
                { value: 0.8, label: '80%' },
                { value: 1, label: '100%' },
                { value: 1.15, label: '115%' },
              ]}
            />
          )}
        </Space>
      </div>

      {viewMode === 'list' ? (
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {steps.map((step, idx) => {
            const key = step.node_key!;
            const cat = getNodeCategory(step.operation);
            const info = summarizeStep(step, models);
            const dry = dryRunByNode?.get(key);
            const outEdges = edges.filter(e => e.source === key);
            return (
              <div
                key={key}
                role={readonly ? undefined : 'button'}
                tabIndex={readonly ? undefined : 0}
                onClick={() => !readonly && onSelectNode?.(key)}
                onKeyDown={e => { if (!readonly && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelectNode?.(key); } }}
                style={{
                  padding: '10px 14px',
                  borderRadius: 8,
                  border: `1px solid ${selectedKey === key ? 'var(--ui-primary)' : 'var(--ui-border)'}`,
                  background: selectedKey === key ? 'var(--ui-surface-raised)' : 'var(--ui-surface)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: 8,
                  cursor: readonly ? 'default' : 'pointer',
                }}
              >
                <Space wrap>
                  <Tag>{idx + 1}</Tag>
                  <Tag color={cat === 'model' ? 'blue' : cat === 'builtin' ? 'cyan' : cat === 'branch' ? 'gold' : 'purple'}>
                    {CATEGORY_LABELS[cat]}
                  </Tag>
                  <Typography.Text strong>{step.title || WORKFLOW_OPERATIONS[step.operation]}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{info.subtitle}</Typography.Text>
                  {info.warning && <Tag color="warning">{info.warning}</Tag>}
                  {dry && (
                    <Tag color={dry.error ? 'error' : 'success'}>
                      {dry.error ? `试运行报错` : `试运行 ${dry.input_count}→${dry.output_count} · ${dry.duration_ms}ms`}
                    </Tag>
                  )}
                </Space>
                <Space size="small" wrap>
                  {outEdges.length > 0 && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      流向：{outEdges.map(e => {
                        const tIdx = steps.findIndex(s => s.node_key === e.target);
                        return `#${tIdx + 1}${e.source_handle !== 'default' ? `(${e.source_handle})` : ''}`;
                      }).join('、')}
                    </Typography.Text>
                  )}
                  {!readonly && onInsertNode && (
                    <Dropdown trigger={['click']} menu={buildInsertMenu({ mode: 'append', sourceKey: key, sourceHandle: 'default' })}>
                      <Button size="small" onClick={e => e.stopPropagation()}>+ 追加下游步骤</Button>
                    </Dropdown>
                  )}
                </Space>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="wf-canvas-viewport">
          <div
            className="wf-canvas-stage"
            style={{
              width: Math.round(layout.width * zoom),
              height: Math.round(layout.height * zoom),
            }}
          >
            <div
              style={{
                width: layout.width,
                height: layout.height,
                transform: zoom === 1 ? undefined : `scale(${zoom})`,
                transformOrigin: '0 0',
                position: 'relative',
              }}
            >
              <svg className="wf-edges-svg" width={layout.width} height={layout.height} aria-hidden="true">
                <defs>
                  <marker id="wf-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#4f6b99" />
                  </marker>
                </defs>
                {layout.edges.map(edge => {
                  const dx = Math.max(36, Math.round((edge.x2 - edge.x1) * 0.48));
                  const d = `M ${edge.x1} ${edge.y1} C ${edge.x1 + dx} ${edge.y1}, ${edge.x2 - dx} ${edge.y2}, ${edge.x2} ${edge.y2}`;
                  const labelW = edge.label ? Math.min(108, edge.label.length * 11 + 14) : 0;
                  const labelY = readonly ? edge.midY : edge.midY - 18;
                  return (
                    <g key={edge.id}>
                      <path className="wf-edge-path" d={d} markerEnd="url(#wf-arrow)" />
                      {edge.label && (
                        <g transform={`translate(${edge.midX - labelW / 2}, ${labelY - 9})`}>
                          <rect className="wf-edge-label-bg" width={labelW} height={18} rx={4} />
                          <text className="wf-edge-label-text" x={labelW / 2} y={13} textAnchor="middle">
                            {edge.label.length > 8 ? edge.label.slice(0, 8) + '…' : edge.label}
                          </text>
                        </g>
                      )}
                    </g>
                  );
                })}
              </svg>

              {!readonly && onInsertNode && layout.edges.map(edge => (
                <Dropdown
                  key={`ins-${edge.id}`}
                  trigger={['click']}
                  menu={buildInsertMenu({
                    mode: edge.source === '__input__' ? 'entry' : 'between',
                    sourceKey: edge.source,
                    sourceHandle: edge.source_handle,
                    targetKey: edge.target,
                  })}
                >
                  <Tooltip title="在此连线中间插入步骤">
                    <button
                      type="button"
                      className="wf-insert-btn"
                      aria-label={`在 ${edge.source} 与 ${edge.target} 之间插入节点`}
                      style={{ left: edge.midX, top: edge.midY }}
                    >
                      +
                    </button>
                  </Tooltip>
                </Dropdown>
              ))}

              {layout.nodes.map(ln => {
                if (ln.isInput) {
                  return (
                    <div
                      key="__input__"
                      className="wf-node-card"
                      data-category="input"
                      data-readonly="true"
                      style={{ left: ln.x, top: ln.y, width: ln.width, height: ln.height, cursor: 'default' }}
                    >
                      <div className="wf-node-top">
                        <span className="wf-node-badge" style={{ background: 'rgba(148,163,184,0.16)', color: '#cbd5e1' }}>起点</span>
                        <span>数据入口</span>
                      </div>
                      <div className="wf-node-title" title={inputLabel}>输入图片</div>
                      <div className="wf-node-sub" title={inputLabel}>{inputLabel}</div>
                    </div>
                  );
                }

                const step = ln.step!;
                const cat = getNodeCategory(step.operation);
                const summary = summarizeStep(step, models);
                const dry = dryRunByNode?.get(ln.key);

                return (
                  <div key={ln.key}>
                    <button
                      type="button"
                      className="wf-node-card"
                      data-category={cat}
                      data-selected={selectedKey === ln.key ? 'true' : 'false'}
                      data-readonly={readonly ? 'true' : 'false'}
                      aria-label={`步骤 ${ln.stepIndex + 1} ${step.title || WORKFLOW_OPERATIONS[step.operation]}`}
                      onClick={() => !readonly && onSelectNode?.(ln.key)}
                      style={{ left: ln.x, top: ln.y, width: ln.width, height: ln.height }}
                    >
                      <div className="wf-node-top">
                        <span className="wf-node-badge">{CATEGORY_LABELS[cat]}</span>
                        <span>#{ln.stepIndex + 1} · {WORKFLOW_OPERATIONS[step.operation]}</span>
                      </div>
                      <div className="wf-node-title" title={step.title || WORKFLOW_OPERATIONS[step.operation]}>
                        {step.title || WORKFLOW_OPERATIONS[step.operation]}
                      </div>
                      {dry ? (
                        <div className="wf-node-dryrun" data-error={dry.error ? 'true' : 'false'} title={dry.error || ''}>
                          {dry.error ? `试运行异常：${dry.error}` : `试运行 ${dry.input_count} → ${dry.output_count} 张 · ${dry.duration_ms}ms`}
                        </div>
                      ) : summary.warning ? (
                        <div className="wf-node-warn">⚠ {summary.warning}</div>
                      ) : (
                        <div className="wf-node-sub" title={summary.subtitle}>{summary.subtitle}</div>
                      )}
                    </button>

                    {!readonly && onInsertNode && ln.handles.map((handle, hIdx) => {
                      const hasOut = outgoingByHandle.has(`${ln.key}:${handle.id}`);
                      const yOff = ln.handles.length <= 1
                        ? ln.height / 2
                        : Math.round(((hIdx + 1) / (ln.handles.length + 1)) * ln.height);
                      if (hasOut && step.operation !== 'branch') {
                        // Still allow adding a parallel downstream branch from the node's output edge via a subtle tail button?
                        // Keep clean: only show '+' at dangling handle or on branch handles without outgoing edge, PLUS an add-branch '+' on top-right if needed.
                        return null;
                      }
                      if (hasOut) return null;
                      return (
                        <Dropdown
                          key={`${ln.key}-${handle.id}-tail`}
                          trigger={['click']}
                          menu={buildInsertMenu({
                            mode: 'append',
                            sourceKey: ln.key,
                            sourceHandle: handle.id,
                          })}
                        >
                          <Tooltip title={step.operation === 'branch' ? `为「${handle.label}」添加下游步骤` : '在末尾追加下一步骤'}>
                            <button
                              type="button"
                              className="wf-insert-btn"
                              aria-label={`从 ${step.title || ln.key} 的 ${handle.label} 追加下游节点`}
                              style={{ left: ln.x + ln.width + 18, top: ln.y + yOff }}
                            >
                              +
                            </button>
                          </Tooltip>
                        </Dropdown>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
