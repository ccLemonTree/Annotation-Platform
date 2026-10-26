import { useCallback, useState } from 'react';
import { Alert, Button, Descriptions, Modal, Select, Space, Table } from 'antd';
import { getAutomationItems } from '../../api/automation';
import type { AutomationItem, AutomationItemStatus, AutomationJob } from '../../api/automation';
import type { PaginatedResponse } from '../../api/projects';
import AutomationEvidence from './AutomationEvidence';
import JsonView from '../JsonView';
import useAutomationFeed from './useAutomationFeed';
import { automationStatusLabels } from './automationStatus';

const activeItems = (data: PaginatedResponse<AutomationItem>) => data.items.some(item => item.status === 'pending' || item.status === 'running');

export default function AutomationJobDetails({ job, onClose }: { job: AutomationJob; onClose: () => void }) {
  const [page, setPage] = useState(1), [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<AutomationItemStatus>();
  const [evidence, setEvidence] = useState<AutomationItem>();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => getAutomationItems(job.id, { page, page_size: pageSize, status }, signal), [job.id, page, pageSize, status]);
  const keepPolling = useCallback((data: PaginatedResponse<AutomationItem>) => job.status === 'pending' || job.status === 'running' || activeItems(data), [job.status]);
  const { data, loading, error } = useAutomationFeed(`${job.id}:${page}:${pageSize}:${status || ''}`, load, keepPolling, revision);
  return <>
    <Modal open title={`任务明细 · ${job.id}`} width={1000} onCancel={onClose} footer={<Button onClick={onClose}>关闭明细</Button>}>
      {job.error && <Alert type="error" title={job.error} />}
      <Descriptions column={2} items={[
        { key: 'status', label: '任务状态', children: automationStatusLabels[job.status] },
        { key: 'progress', label: '进度', children: `${job.processed} / ${job.total}` },
      ]} />
      <details><summary>本次任务参数</summary><JsonView label="本次任务参数" value={job.config}/></details>
      <Space wrap style={{ marginBlock: 16 }}>
        <Select aria-label="明细状态" allowClear placeholder="全部明细状态" value={status} style={{ width: 180 }}
          options={(['pending', 'running', 'approved', 'needs_review', 'failed', 'skipped', 'canceled'] as const).map(value => ({ value, label: automationStatusLabels[value] }))}
          onChange={value => { setStatus(value); setPage(1); }} />
        <Button loading={loading} onClick={() => setRevision(n => n + 1)}>刷新明细</Button>
      </Space>
      {error && <Alert type="error" title="明细读取失败，请重试。" />}
      <Table rowKey="id" size="small" loading={loading} dataSource={data?.items || []} scroll={{ x: 750 }}
        columns={[
          { title: '资产 ID', dataIndex: 'asset_id', width: 230 },
          { title: '状态', dataIndex: 'status', render: value => automationStatusLabels[value] || value },
          { title: '原因', dataIndex: 'reason' },
          { title: '标注版本', dataIndex: 'annotation_version', render: value => value ?? '—' },
          { title: '操作', render: (_, item) => <Button onClick={() => setEvidence(item)}>证据详情</Button> },
        ]} pagination={{ current: page, pageSize, total: data?.total || 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100],
          onChange: (next, size) => { setPage(size === pageSize ? next : 1); setPageSize(size); } }} />
    </Modal>
    {evidence && <Modal open title="证据详情" width={760} styles={{ body: { maxHeight: '65dvh', overflowY: 'auto' } }} onCancel={() => setEvidence(undefined)} footer={<Button onClick={() => setEvidence(undefined)}>关闭证据</Button>}><AutomationEvidence item={evidence} /></Modal>}
  </>;
}
