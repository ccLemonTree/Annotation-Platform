import { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Progress, Select } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { getGpuResources, getGpuServers } from '../../api/gpuResources';
import type { GpuResources, GpuServer } from '../../api/gpuResources';

export default function DashboardGpu({ admin }: { admin: boolean }) {
  const [servers, setServers] = useState<GpuServer[]>([]), [serverId, setServerId] = useState<string>();
  const [data, setData] = useState<GpuResources>(), [gpuId, setGpuId] = useState<string>();
  const [loading, setLoading] = useState(admin), [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    getGpuServers(controller.signal).then(rows => {
      if (controller.signal.aborted) return;
      const enabled = rows.filter(server => server.enabled);
      setServers(enabled); setServerId(id => enabled.some(server => server.id === id) ? id : enabled[0]?.id);
      if (!enabled.length) setLoading(false);
    }).catch(() => { if (!controller.signal.aborted) { setError('服务器列表读取失败，请刷新重试'); setLoading(false); } });
    return () => controller.abort();
  }, [admin, revision]);
  useEffect(() => {
    if (!admin || !serverId) return;
    const controller = new AbortController();
    const load = async () => {
      setLoading(true); setData(undefined); setError('');
      try {
        const snapshot = await getGpuResources(controller.signal, serverId);
        if (!controller.signal.aborted) { setData(snapshot); setGpuId(id => snapshot.gpus.some(gpu => gpu.uuid === id) ? id : snapshot.gpus[0]?.uuid); }
      } catch { if (!controller.signal.aborted) setError('算力状态读取失败，请检查连接后刷新重试'); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    };
    void load();
    return () => controller.abort();
  }, [admin, serverId, revision]);
  const gpu = data?.gpus.find(item => item.uuid === gpuId);
  const memory = gpu?.memory_used_mib != null && gpu.memory_total_mib != null
    ? `${(gpu.memory_used_mib / 1024).toFixed(1)} / ${(gpu.memory_total_mib / 1024).toFixed(1)} GB` : '--';
  return <Card className="dashboard-panel dashboard-compute" title={<h2>算力概况</h2>} extra={admin && <Button type="text" aria-label="刷新算力概况" icon={<ReloadOutlined />} loading={loading} onClick={() => setRevision(value => value + 1)} />}>
    {!admin ? <Empty description="仅管理员可查看算力状态" /> : <>
      {error && <Alert type="error" showIcon title={error} />}
      {servers.length > 0 && <div className="dashboard-gpu-selectors"><Select aria-label="概览服务器" value={serverId} onChange={setServerId} options={servers.map(server => ({ value: server.id, label: server.name }))} />
        {data && <Select aria-label="概览显卡" value={gpuId} onChange={setGpuId} options={data.gpus.map(item => ({ value: item.uuid, label: `GPU ${item.index} · ${item.name}` }))} />}</div>}
      {loading ? <div className="dashboard-gpu-loading" role="status">正在读取GPU状态…</div> : gpu ? <>
        <div className="dashboard-resource-body"><div className="dashboard-gauge"><Progress type="circle" percent={gpu.utilization_percent ?? 0} size={124} strokeWidth={6} strokeColor="var(--ui-primary)" format={() => gpu.utilization_percent == null ? '--' : `${gpu.utilization_percent}%`} /><span>GPU {gpu.index} 利用率</span></div>
          <dl><div><dt>显存占用</dt><dd>{memory}</dd></div><div><dt>温度</dt><dd>{gpu.temperature_c == null ? '--' : `${gpu.temperature_c} °C`}</dd></div>
            <div><dt>计算进程</dt><dd>{data?.processes_complete ? `${data.processes.filter(process => process.gpu_uuid === gpu.uuid).length} 个` : '未完整采集'}</dd></div></dl></div>
        <p className="dashboard-panel-note">采集于 {new Date(data!.collected_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}{data!.cached ? ' · 缓存快照' : ''}</p>
        {data!.warnings.length > 0 && <p className="dashboard-panel-note">{data!.warnings.join('；')}</p>}
      </> : !error && <Empty description={servers.length ? '本次未发现GPU' : '暂无已启用的GPU服务器'} />}
      <Link className="dashboard-resource-link" to="/gpu-resources">查看 GPU 资源 →</Link>
    </>}
  </Card>;
}
