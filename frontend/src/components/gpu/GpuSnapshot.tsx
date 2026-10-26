import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Progress, Result, Row, Space, Spin, Statistic, Switch, Table, Tag, Typography } from 'antd';
import axios from 'axios';
import { getGpuResources } from '../../api/gpuResources';
import type { GpuProcess, GpuResource, GpuResources } from '../../api/gpuResources';

const metric = (value: number | null, unit = '') => value == null ? '--' : `${value.toLocaleString()}${unit}`;
const gpuUuid = (value: string) => <Typography.Text copyable style={{ overflowWrap: 'anywhere' }}>{value}</Typography.Text>;

export default function GpuSnapshot({ serverId = 'default', onForbidden, onReleaseProcess, refreshKey = 0, releaseBusy = false }: { serverId?: string; onForbidden?: () => void; onReleaseProcess?: (process: GpuProcess) => void; refreshKey?: number; releaseBusy?: boolean } = {}) {
  const [admin, setAdmin] = useState(() => {
    try { return JSON.parse(localStorage.getItem('user') || 'null')?.role === 'admin'; }
    catch { return false; }
  });
  const [data, setData] = useState<GpuResources>();
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [visible, setVisible] = useState(!document.hidden);
  const active = useRef(false), request = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!admin || !active.current || document.hidden || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const result = await getGpuResources(controller.signal, serverId);
      if (active.current && request.current === controller && !controller.signal.aborted) {
        setData(result); setError('');
      }
    } catch (cause) {
      if (!active.current || request.current !== controller || controller.signal.aborted) return;
      if (axios.isAxiosError(cause)) {
        if (cause.response?.status === 403) { setData(undefined); setAdmin(false); onForbidden?.(); return; }
        const detail = cause.response?.data?.detail;
        setError(typeof detail === 'string' && detail.trim() ? detail
          : cause.code === 'ECONNABORTED' || cause.code === 'ETIMEDOUT' || cause.response?.status === 504
            ? 'GPU资源采集超时，请稍后重试。' : 'GPU资源读取失败，请检查服务配置或连接后重试。');
      } else { setError('GPU资源读取失败，请稍后重试。'); }
    } finally {
      if (active.current && request.current === controller) {
        request.current = null; setLoading(false);
      }
    }
  }, [admin, serverId, onForbidden]);

  useEffect(() => {
    active.current = true;
    const visibility = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', visibility);
    void load();
    return () => {
      active.current = false;
      request.current?.abort(); request.current = null;
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [load, refreshKey]);

  useEffect(() => {
    if (!admin || !autoRefresh || !visible) return;
    void load();
    const timer = setInterval(() => { void load(); }, 30000);
    return () => clearInterval(timer);
  }, [admin, autoRefresh, visible, load]);

  if (!admin) return <Result status="403" title="无权限" subTitle="仅管理员可查看GPU资源，当前无权限，不采集GPU资源。" />;

  const memorySum = (field: 'memory_total_mib' | 'memory_free_mib') => {
    if (!data || data.gpus.some(gpu => gpu[field] == null)) return '--';
    return data.gpus.reduce((sum, gpu) => sum + gpu[field]!, 0).toLocaleString();
  };
  const gpuColumns = [
    { title: 'GPU索引', dataIndex: 'index', width: 90 },
    { title: '型号 / 驱动', dataIndex: 'name', width: 180, render: (name: string, gpu: GpuResource) =>
      <Space orientation="vertical" size={0}><Typography.Text>{name || '--'}</Typography.Text><Typography.Text type="secondary">驱动：{gpu.driver_version || '--'}</Typography.Text></Space> },
    { title: 'GPU UUID', dataIndex: 'uuid', width: 330, render: gpuUuid },
    { title: '显存（MiB）', dataIndex: 'memory_used_mib', width: 270, render: (_: number | null, gpu: GpuResource) =>
      <div>
        <div>已用 {metric(gpu.memory_used_mib)} / 总量 {metric(gpu.memory_total_mib)}</div>
        {gpu.memory_used_mib != null && gpu.memory_total_mib != null && gpu.memory_total_mib > 0
          ? <Progress percent={Math.min(100, Math.max(0, gpu.memory_used_mib / gpu.memory_total_mib * 100))} format={percent => `${percent?.toFixed(1)}%`} size="small" />
          : <Typography.Text type="secondary">显存占比 --</Typography.Text>}
        <Typography.Text type="secondary">实际剩余 {metric(gpu.memory_free_mib)}</Typography.Text>
      </div> },
    { title: '利用率', dataIndex: 'utilization_percent', width: 100, render: (value: number | null) => metric(value, '%') },
    { title: '温度', dataIndex: 'temperature_c', width: 90, render: (value: number | null) => metric(value, ' °C') },
    { title: '功耗 / 上限', dataIndex: 'power_draw_w', width: 160, render: (value: number | null, gpu: GpuResource) => `${metric(value, ' W')} / ${metric(gpu.power_limit_w, ' W')}` },
    { title: 'MIG模式', dataIndex: 'mig_mode', width: 120, render: (value: string) => value || '--' },
  ];
  const processColumns = [
    { title: 'GPU索引', dataIndex: 'gpu_uuid', width: 100, render: (uuid: string) => data?.gpus.find(gpu => gpu.uuid === uuid)?.index ?? '--' },
    { title: 'GPU UUID', dataIndex: 'gpu_uuid', width: 330, render: gpuUuid },
    { title: 'PID', dataIndex: 'pid', width: 110 },
    { title: '进程名称', dataIndex: 'name', render: (value: string) => <Typography.Text style={{ overflowWrap: 'anywhere' }}>{value || '--'}</Typography.Text> },
    { title: '显存（MiB）', dataIndex: 'memory_used_mib', width: 150, render: (value: number | null) => metric(value) },
    ...(onReleaseProcess ? [{ title: '操作', key: 'actions', width: 140, fixed: 'right' as const, render: (_: unknown, process: GpuProcess) => <Button danger aria-label={`释放进程 ${process.pid}`} disabled={releaseBusy || loading || !!error || !data?.processes_complete || !process.gpu_uuid.startsWith('GPU-')} onClick={() => onReleaseProcess(process)}>释放显存</Button> }] : []),
  ];

  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0, fontVariantNumeric: 'tabular-nums' }}>
    <Alert type="info" showIcon title={onReleaseProcess ? 'GPU资源监控与手动释放' : 'GPU只读监控'} description="查看当前GPU状态及计算进程。低利用率仍可能有模型占用显存。" />
    <Space wrap>
      <Button type="primary" aria-label={error && !data ? '重试' : '手动刷新'} onClick={() => { void load(); }} loading={loading} disabled={!visible}>{error && !data ? '重试' : '手动刷新'}</Button>
      <Switch aria-label="自动30秒刷新" checked={autoRefresh} onChange={setAutoRefresh} />
      <Typography.Text>自动30秒刷新</Typography.Text>
      <Typography.Text type="secondary">{!visible ? '页面隐藏，已暂停自动刷新' : autoRefresh ? '请求进行中不会重复采集' : '自动刷新已关闭，数据仅代表上次采集'}</Typography.Text>
    </Space>
    {error && <Alert type="error" showIcon title={data ? '刷新失败：上次数据已陈旧' : 'GPU资源读取失败'} description={error} />}
    {!data && !error && loading && <Spin description="正在采集GPU资源…"><div style={{ minHeight: 100 }} /></Spin>}
    {data && <>
      <Card title="资源概览">
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} items={[
          { key: 'server', label: '服务器', children: data.server_name || '--' },
          { key: 'time', label: '更新时间（北京时间）', children: <Typography.Text title={data.collected_at}>{data.collected_at ? new Date(data.collected_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '--'}</Typography.Text> },
          { key: 'cached', label: '数据来源', children: <Tag color={data.cached ? 'orange' : 'blue'}>{data.cached ? '缓存结果' : '本次采集'}</Tag> },
        ]} />
        <Row gutter={[24, 16]}>
          <Col xs={24} sm={8}><Statistic title="总GPU" value={data.gpus.length} suffix="张" /></Col>
          <Col xs={24} sm={8}><Statistic title="总显存" value={memorySum('memory_total_mib')} suffix="MiB" /></Col>
          <Col xs={24} sm={8}><Statistic title="实际剩余显存" value={memorySum('memory_free_mib')} suffix="MiB" /></Col>
        </Row>
      </Card>
      {data.warnings.length > 0 && <Alert type="warning" showIcon title="采集提示" description={<ul style={{ marginBottom: 0 }}>{data.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>} />}
      <Card title="GPU逐卡状态">
        <Table<GpuResource> rowKey="uuid" dataSource={data.gpus} columns={gpuColumns} scroll={{ x: 1440 }} pagination={false} locale={{ emptyText: '本次采集未发现GPU' }} />
      </Card>
      <Card title="计算进程" extra={<Tag color={data.processes_complete ? 'green' : 'orange'}>{data.processes_complete ? '进程采集完整' : '进程采集不完整'}</Tag>}>
        {!data.processes_complete && <Alert type="warning" showIcon title="计算进程未采集或采集不完整" description="以下仅展示已获得的计算进程，空表不代表没有进程；图形进程不在本表范围内。" style={{ marginBottom: 16 }} />}
        <Table<GpuProcess> rowKey={process => `${process.gpu_uuid}:${process.pid}`} dataSource={data.processes} columns={processColumns} scroll={{ x: 960 }} pagination={{ pageSize: 10 }}
          locale={{ emptyText: data.processes_complete ? '无计算进程（本次采集）' : '未采集到完整计算进程信息' }} />
      </Card>
    </>}
  </Space>;
}
