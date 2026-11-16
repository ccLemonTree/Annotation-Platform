import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Result, Select, Space, Spin, Tag, Typography } from 'antd';
import { getGpuServers } from '../api/gpuResources';
import type { GpuServer } from '../api/gpuResources';
import GpuReleaseWorkspace from '../components/gpu/GpuReleaseWorkspace';
import { connectionError, forbidden } from '../components/gpu/connectionError';
import { Link,useSearchParams } from 'react-router-dom';

export default function GpuResourcesPage() {
  const [params]=useSearchParams();
  const [admin, setAdmin] = useState(() => {
    try { return JSON.parse(localStorage.getItem('user') || 'null')?.role === 'admin'; } catch { return false; }
  });
  const [servers, setServers] = useState<GpuServer[]>([]), [selectedId, setSelectedId] = useState(params.get('server_id')||'default');
  const [loading, setLoading] = useState(true), [listError, setListError] = useState(''), [reload, setReload] = useState(0);
  const onForbidden = useCallback(() => {
    setAdmin(false); setServers([]); setListError('');
  }, []);
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    getGpuServers(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const legacy = result.find(server => server.id === 'default');
      const managed = legacy && result.find(server => server.source === 'password' && server.name.toLowerCase() === legacy.name.toLowerCase());
      const available = managed ? result.filter(server => server.id !== 'default') : result;
      setServers(available); setSelectedId(id => id === 'default' && managed ? managed.id : available.some(server => server.id === id) ? id : available[0]?.id || 'default');
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (forbidden(cause)) onForbidden(); else setListError(connectionError(cause, '服务器列表读取失败，请重试。'));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [admin, reload, onForbidden]);
  const selected = servers.find(server => server.id === selectedId);
  if (!admin) return <Result status="403" title="无权限" subTitle="仅管理员可管理服务器和查看GPU资源。" />;
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    <Card title="GPU服务器连接" styles={{ body: { minWidth: 0 } }}>
      <Space wrap style={{ width: '100%', marginBottom: 16 }}>
        <Select aria-label="GPU服务器" value={selected?.id} loading={loading} placeholder="搜索连接名称或IP"
          showSearch={{ filterOption: (input, option) => (option?.searchText || '').includes(input.trim().toLowerCase()) }} notFoundContent="无匹配服务器"
          style={{ width: 260, maxWidth: '100%' }} onChange={id => setSelectedId(id)}
          options={servers.map(server => ({ value: server.id, label: server.name, searchText: [server.name, server.host, server.jump?.host].filter(Boolean).join(' ').toLowerCase() }))} />
        <Link to="/gpu-servers">服务器管理</Link>
        {selected && <Link to={`/gpu-terminal?server_id=${encodeURIComponent(selected.id)}`}>SSH调试</Link>}
      </Space>
      {loading && !servers.length && <Spin />}
      {listError && <Alert type="error" showIcon title={listError} action={<Button onClick={() => { setLoading(true); setListError(''); setReload(n => n + 1); }}>重试列表</Button>} />}
      {!loading && !listError && !servers.length && <Typography.Paragraph type="secondary">暂无服务器，请在服务器管理中新增连接。</Typography.Paragraph>}
      {selected && <Space wrap>
        {selected.source === 'ssh_manager' ? <Tag>原ssh-manager连接 · 配置只读</Tag> : <>
          <Typography.Text style={{ overflowWrap: 'anywhere' }}>{selected.username}@{selected.host} · 端口 {selected.port}</Typography.Text>
          {selected.jump && <Tag>经跳板机 {selected.jump.host}:{selected.jump.port}</Tag>}
          <Tag>{selected.has_password ? '已设置密码' : '未设置密码'}</Tag>
          <Tag>{selected.enabled ? '已启用采集' : '已停用采集'}</Tag>
        </>}
      </Space>}
    </Card>
    {selected && (!selected.enabled ? <Alert type="info" showIcon title="此连接已停用，请在服务器管理中启用采集。" />
      : selected.source === 'password' && !selected.has_password
        ? <Alert type="info" showIcon title="此连接未设置密码，请在服务器管理中填写密码后采集。" />
        : <GpuReleaseWorkspace key={`${selected.id}:${selected.revision}`} serverId={selected.id} onForbidden={onForbidden} />)}
  </Space>;
}
