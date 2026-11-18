import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Popconfirm, Progress, Space, Table } from 'antd';
import { isAxiosError } from 'axios';
import apiClient, { API_BASE_URL } from '../api/client';
import { useCurrentUser } from './currentUser';

interface FrozenDataset {
  id: string;
  format: string;
  created_at: string;
  version_label?: string | null;
  asset_count: number;
  sample_count?: number | null;
}

interface FreezeJob {
  id: string;
  format: string;
  version_label: string;
  status: 'queued' | 'preparing' | 'packing' | 'saving' | 'completed' | 'failed';
  packed: number;
  total: number;
  error?: string;
}

const jobStatus = { queued: '排队中', preparing: '正在确认冻结范围', packing: '正在打包',
  saving: '正在保存冻结版本', completed: '已完成', failed: '冻结失败' };

const formats: Record<string, string> = {
  yolo: 'YOLO', coco: 'COCO', swift_sft: '大模型 SFT', infonce: 'InfoNCE',
  contrastive: 'Contrastive', online_contrastive: 'Online Contrastive', cosine_similarity: 'Cosine Similarity',
};

export default function FrozenDatasetList({ projectId, revision, onDeleted, downloads, onDownloadHandled }: {
  projectId: string; revision: number; onDeleted: () => void;
  downloads: string[]; onDownloadHandled: (id: string) => void;
}) {
  const admin = useCurrentUser()?.role === 'admin';
  const [rows, setRows] = useState<FrozenDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [downloading, setDownloading] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [jobs, setJobs] = useState<FreezeJob[]>([]);
  const [jobError, setJobError] = useState('');
  const [completed, setCompleted] = useState('');
  const startedDownloads = useRef(new Set<string>());

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      let retry = true;
      try {
        const { data } = await apiClient.get<FreezeJob[]>(`/api/export/projects/${projectId}/snapshot-jobs`, {
          signal: controller.signal, skipErrorToast: true,
        });
        if (controller.signal.aborted) return;
        setJobError('');
        setJobs(data);
        setCompleted(data.filter(job => job.status === 'completed').map(job => job.id).join(','));
        retry = data.some(job => job.status !== 'completed' && job.status !== 'failed');
      } catch {
        if (!controller.signal.aborted) setJobError('冻结任务进度读取失败，正在重试。');
      } finally {
        if (retry && !controller.signal.aborted) timer = window.setTimeout(poll, 2500);
      }
    };
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [projectId, revision, reload]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    apiClient.get<FrozenDataset[]>(`/api/export/projects/${projectId}/snapshots`, {
      params: { summary_only: true }, signal: controller.signal, skipErrorToast: true,
    }).then(({ data }) => setRows(data))
      .catch(() => { if (!controller.signal.aborted) setError('冻结版本读取失败，请刷新重试。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, revision, reload, completed]);

  const download = async (id: string, automatic = false) => {
    setDownloading(previous => [...previous, id]);
    setError('');
    try {
      const path = `/api/export/snapshots/${id}/browser-download`;
      await apiClient.post(path, undefined, { skipErrorToast: true, withCredentials: true });
      const link = document.createElement('a');
      link.href = new URL(`${API_BASE_URL.replace(/\/$/, '')}${path}`, window.location.origin).href;
      link.click();
    } catch (cause) {
      const detail = isAxiosError(cause) ? cause.response?.data?.detail : undefined;
      setError(typeof detail === 'string' ? detail : automatic
        ? '冻结版本已保存，自动下载失败，请从列表重新下载。' : '下载冻结版本失败，请重试。');
    } finally { setDownloading(previous => previous.filter(pending => pending !== id)); }
  };

  useEffect(() => {
    for (const job of jobs) {
      if (!downloads.includes(job.id) || startedDownloads.current.has(job.id)) continue;
      if (job.status === 'completed') {
        startedDownloads.current.add(job.id);
        void download(job.id, true);
        onDownloadHandled(job.id);
      } else if (job.status === 'failed') {
        onDownloadHandled(job.id);
      }
    }
  }, [jobs, downloads, onDownloadHandled]);

  const removeSnapshot = async (row: FrozenDataset) => {
    setBusy(row.id);
    setError('');
    const url = `/api/export/snapshots/${row.id}`;
    try {
      await apiClient.delete(url, { skipErrorToast: true });
      setRows(previous => previous.filter(item => item.id !== row.id));
      onDeleted();
    } catch (cause) {
      const detail = isAxiosError(cause) ? cause.response?.data?.detail : undefined;
      setError(typeof detail === 'string' ? detail : '删除冻结版本失败，请重试。');
    } finally { setBusy(''); }
  };

  return <Card title="冻结版本" extra={<Button size="small" disabled={loading || !!busy} onClick={() => setReload(value => value + 1)}>刷新</Button>}>
    {jobError && <Alert type="warning" showIcon title={jobError} style={{ marginBottom: 12 }} />}
    {jobs.filter(job => job.status !== 'completed').map(job => <Alert key={job.id} showIcon
      style={{ marginBottom: 12 }} type={job.status === 'failed' ? 'error' : 'info'}
      title={`${job.version_label || formats[job.format] || job.format} · ${jobStatus[job.status]}`}
      description={<>
        <div>已打包 {job.packed.toLocaleString('zh-CN')} / {job.total ? job.total.toLocaleString('zh-CN') : '待确认'} 张</div>
        {!!job.total && <Progress percent={Math.floor(job.packed / job.total * 100)}
          status={job.status === 'failed' ? 'exception' : 'active'} />}
        {job.error}
      </>} />)}
    {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
    <Table<FrozenDataset> rowKey="id" size="small" dataSource={rows} loading={loading} pagination={{ pageSize: 10, hideOnSinglePage: true }}
      locale={{ emptyText: error ? '无法读取冻结版本' : '暂无冻结版本' }} scroll={{ x: 650 }} columns={[
        { title: '版本', render: (_, row) => <>{row.version_label || row.id.slice(0, 8)}{row.version_label && <div><small>{row.id.slice(0, 8)}</small></div>}</> },
        { title: '格式', render: (_, row) => formats[row.format] || row.format },
        { title: '数量', render: (_, row) => row.sample_count == null ? `${row.asset_count} 张` : `${row.sample_count} 条 · ${row.asset_count} 张` },
        { title: '冻结时间', render: (_, row) => new Date(row.created_at).toLocaleString('zh-CN') },
        { title: '操作', render: (_, row) => <Space>
          <Button size="small" loading={downloading.includes(row.id)} disabled={busy === row.id}
            onClick={() => void download(row.id)}>下载</Button>
          {admin && <Popconfirm title="删除此冻结版本？" description="删除冻结包和版本记录，原图与项目标注保留。"
            okText="删除" cancelText="取消" onConfirm={() => removeSnapshot(row)} disabled={!!busy || loading || downloading.includes(row.id)}>
            <Button size="small" danger disabled={!!busy || loading || downloading.includes(row.id)} aria-label={`删除冻结版本 ${row.version_label || row.id}`}>删除</Button>
          </Popconfirm>}
        </Space> },
      ]} />
  </Card>;
}
