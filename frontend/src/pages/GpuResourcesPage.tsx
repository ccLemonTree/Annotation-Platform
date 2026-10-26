import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Modal, Result, Select, Space, Spin, Switch, Tag, Typography } from 'antd';
import { deleteGpuServer, getGpuServers, updateGpuServer } from '../api/gpuResources';
import type { GpuServer } from '../api/gpuResources';
import GpuReleaseWorkspace from '../components/gpu/GpuReleaseWorkspace';
import GpuServerForm from '../components/gpu/GpuServerForm';
import { connectionError, forbidden } from '../components/gpu/connectionError';
import { Link,useSearchParams } from 'react-router-dom';

export default function GpuResourcesPage() {
  const [params]=useSearchParams();
  const [admin, setAdmin] = useState(() => {
    try { return JSON.parse(localStorage.getItem('user') || 'null')?.role === 'admin'; } catch { return false; }
  });
  const [servers, setServers] = useState<GpuServer[]>([]), [selectedId, setSelectedId] = useState(params.get('server_id')||'default');
  const [loading, setLoading] = useState(true), [listError, setListError] = useState(''), [reload, setReload] = useState(0);
  const [editing, setEditing] = useState<GpuServer | null>(), [deleting, setDeleting] = useState<GpuServer>();
  const [busy, setBusy] = useState(false), [actionError, setActionError] = useState('');
  const mounted = useRef(false), writing = useRef(false);
  const onForbidden = useCallback(() => {
    setAdmin(false); setServers([]); setEditing(undefined); setDeleting(undefined); setActionError(''); setListError('');
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    getGpuServers(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setServers(result); setSelectedId(id => result.some(server => server.id === id) ? id : result[0]?.id || 'default');
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (forbidden(cause)) onForbidden(); else setListError(connectionError(cause, '服务器列表读取失败，请重试。'));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [admin, reload, onForbidden]);
  const selected = servers.find(server => server.id === selectedId);
  const saved = (server: GpuServer) => {
    setServers(list => list.some(item => item.id === server.id) ? list.map(item => item.id === server.id ? server : item) : [...list, server]);
    setSelectedId(server.id); setEditing(undefined); setActionError('');
  };
  const change = async (server: GpuServer, remove = false) => {
    if (writing.current || server.source !== 'password') return;
    writing.current = true; setBusy(true); setActionError('');
    try {
      if (remove) {
        await deleteGpuServer(server.id);
        if (mounted.current) {
          setServers(list => list.filter(item => item.id !== server.id)); setDeleting(undefined);
          setSelectedId(servers.find(item => item.id !== server.id)?.id || 'default');
        }
      } else {
        const updated = await updateGpuServer(server.id, { enabled: !server.enabled });
        if (mounted.current) setServers(list => list.map(item => item.id === server.id ? updated : item));
      }
    } catch (cause) {
      if (mounted.current) {
        if (forbidden(cause)) onForbidden(); else setActionError(connectionError(cause, remove ? '连接删除失败，请重试。' : '采集状态修改失败，请重试。'));
      }
    } finally { writing.current = false; if (mounted.current) setBusy(false); }
  };
  if (!admin) return <Result status="403" title="无权限" subTitle="仅管理员可管理服务器和查看GPU资源。" />;
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    <Card title="GPU服务器连接" styles={{ body: { minWidth: 0 } }}>
      <Space wrap style={{ width: '100%', marginBottom: 16 }}>
        <Select aria-label="GPU服务器" value={selected?.id} loading={loading} disabled={busy} placeholder="请选择服务器"
          style={{ width: 260, maxWidth: '100%' }} onChange={id => { setSelectedId(id); setActionError(''); }}
          options={servers.map(server => ({ value: server.id, label: server.name }))} />
        <Button type="primary" disabled={loading || busy} onClick={() => { setActionError(''); setEditing(null); }}>新增服务器</Button>
        {selected && <Link to={`/gpu-terminal?server_id=${encodeURIComponent(selected.id)}`}>SSH调试</Link>}
        {selected?.source === 'password' && <>
          <Button disabled={busy} onClick={() => { setActionError(''); setEditing(selected); }}>编辑连接</Button>
          <Button danger disabled={busy} onClick={() => { setActionError(''); setDeleting(selected); }}>删除连接</Button>
        </>}
      </Space>
      {loading && !servers.length && <Spin />}
      {listError && <Alert type="error" showIcon title={listError} action={<Button onClick={() => { setLoading(true); setListError(''); setReload(n => n + 1); }}>重试列表</Button>} />}
      {!loading && !listError && !servers.length && <Typography.Paragraph type="secondary">暂无服务器，请新增连接。</Typography.Paragraph>}
      {selected && <Space wrap>
        {selected.source === 'ssh_manager' ? <Tag>原ssh-manager连接 · 配置只读</Tag> : <>
          <Typography.Text style={{ overflowWrap: 'anywhere' }}>{selected.username}@{selected.host} · 端口 {selected.port}</Typography.Text>
          <Tag>{selected.has_password ? '已设置密码' : '未设置密码'}</Tag>
          <Space>
            <Switch aria-label="启用采集" checked={selected.enabled} loading={busy} disabled={editing !== undefined || !!deleting} onChange={() => { void change(selected); }} />
            <Typography.Text>启用采集</Typography.Text>
          </Space>
        </>}
      </Space>}
      {actionError && !deleting && <Alert type="error" showIcon title={actionError} style={{ marginTop: 16 }} />}
    </Card>
    {selected && (!selected.enabled ? <Alert type="info" showIcon title="此连接已停用，启用后自动采集。" />
      : selected.source === 'password' && !selected.has_password
        ? <Alert type="info" showIcon title="此连接未设置密码，请填写密码后采集。" action={<Button onClick={() => setEditing(selected)}>填写密码</Button>} />
        : <GpuReleaseWorkspace key={`${selected.id}:${selected.revision}`} serverId={selected.id} onForbidden={onForbidden} />)}
    {editing !== undefined && <GpuServerForm server={editing || undefined} onClose={() => setEditing(undefined)} onSaved={saved} onForbidden={onForbidden} />}
    {deleting && <Modal open title="删除GPU服务器？" okText="确认删除" cancelText="取消" okButtonProps={{ danger: true, 'aria-label': '确认删除' }} confirmLoading={busy}
      mask={{ closable: false }} closable={!busy} cancelButtonProps={{ disabled: busy }} onCancel={() => { if (!busy) { setDeleting(undefined); setActionError(''); } }} onOk={() => { void change(deleting, true); }}>
      <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>删除连接“{deleting.name}”及其保存的密码？</Typography.Paragraph>
      {actionError && <Alert type="error" showIcon title={actionError} />}
    </Modal>}
  </Space>;
}
