import { useCallback, useState } from 'react';
import { Alert, Button, Checkbox, Descriptions, Modal, Select, Space, Table } from 'antd';
import { isAxiosError } from 'axios';
import { followAutomationUploads, getAutomationItems } from '../../api/automation';
import type { AutomationItem, AutomationItemStatus, AutomationJob } from '../../api/automation';
import type { PaginatedResponse } from '../../api/projects';
import AutomationEvidence from './AutomationEvidence';
import JsonView from '../JsonView';
import useAutomationFeed from './useAutomationFeed';
import { automationStatusLabels } from './automationStatus';

const activeItems = (data: PaginatedResponse<AutomationItem>) => data.items.some(item => item.status === 'pending' || item.status === 'running');

export default function AutomationJobDetails({ job, onClose, onFollowSaved }: {
  job: AutomationJob; onClose: () => void; onFollowSaved?: (enabled:boolean) => void
}) {
  const [page, setPage] = useState(1), [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<AutomationItemStatus>();
  const [evidence, setEvidence] = useState<AutomationItem>();
  const [revision, setRevision] = useState(0);
  const [savingFollow,setSavingFollow]=useState(false),[followError,setFollowError]=useState('');
  const load = useCallback((signal: AbortSignal) => getAutomationItems(job.id, { page, page_size: pageSize, status }, signal), [job.id, page, pageSize, status]);
  const keepPolling = useCallback((data: PaginatedResponse<AutomationItem>) => !!job.follow_uploads || job.status === 'pending' || job.status === 'running' || activeItems(data), [job.status,job.follow_uploads]);
  const { data, loading, error } = useAutomationFeed(`${job.id}:${page}:${pageSize}:${status || ''}`, load, keepPolling, revision);
  const setFollow=async(enabled:boolean)=>{
    setSavingFollow(true);setFollowError('');
    try{onFollowSaved?.((await followAutomationUploads(job.id,enabled)).follow_uploads);}
    catch(error){const detail=isAxiosError(error)?error.response?.data?.detail:undefined;setFollowError(typeof detail==='string'?detail:'接收新图设置保存失败，请重试');}
    finally{setSavingFollow(false);}
  };
  return <>
    <Modal open title={`任务明细 · ${job.id}`} width={1000} onCancel={onClose} footer={<Button onClick={onClose}>关闭明细</Button>}>
      {job.error && <Alert type="error" title={job.error} />}
      <Descriptions column={2} items={[
        { key: 'status', label: '任务状态', children: automationStatusLabels[job.status] },
        { key: 'progress', label: '进度', children: `${job.processed} / ${job.total}` },
      ]} />
      {onFollowSaved&&job.status!=='canceled'&&<div style={{marginBlock:12}}>
        <Checkbox checked={!!job.follow_uploads} disabled={savingFollow} onChange={event=>void setFollow(event.target.checked)}>自动加入新上传图片</Checkbox>
        <p className="workspace-help">{job.kind==='review'?'后续上传且带入有效标注的新图自动进入审核。':'后续上传并加入本项目的新图沿用本任务的模型和规则。'}关闭后，已入队的图片继续处理。</p>
        {followError&&<Alert type="error" showIcon title={followError}/>}
      </div>}
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
