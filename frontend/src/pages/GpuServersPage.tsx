import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Card, Descriptions, Input, Modal, Result, Space, Switch, Table, Tag, Typography, Upload } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { deleteGpuServer, getGpuServers, importGpuServers, updateGpuServer } from '../api/gpuResources';
import type { GpuServer, GpuServerInput } from '../api/gpuResources';
import GpuServerForm from '../components/gpu/GpuServerForm';
import { connectionError, forbidden } from '../components/gpu/connectionError';
import { useCurrentUser } from '../components/currentUser';

function downloadTemplate() {
  const data = { servers: [{ name: '训练服务器示例', host: '192.0.2.10', port: 22, username: 'root', password: '请替换为实际密码', enabled: true,
    jump: { host: '192.0.2.1', port: 22, username: 'root', password: '请替换为跳板机密码；直连时删除jump对象' } }] };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })), link = document.createElement('a');
  link.href = url; link.download = 'gpu-servers-template.json'; document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export default function GpuServersPage() {
  const currentUser = useCurrentUser();
  const [forbiddenAccess, setForbiddenAccess] = useState(false);
  const admin = currentUser?.role === 'admin' && !forbiddenAccess;
  const [servers, setServers] = useState<GpuServer[]>([]), [loading, setLoading] = useState(true), [reload, setReload] = useState(0);
  const [search,setSearch]=useState(''),[page,setPage]=useState(1);
  const [listError, setListError] = useState(''), [actionError, setActionError] = useState(''), [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<GpuServer | null>(), [deleting, setDeleting] = useState<GpuServer>(), [busy, setBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false), [importRows, setImportRows] = useState<GpuServerInput[]>([]), [importError, setImportError] = useState(''), [reading, setReading] = useState(false);
  const mounted = useRef(false), writing = useRef(false);
  const onForbidden = useCallback(() => {
    setForbiddenAccess(true); setServers([]); setEditing(undefined); setDeleting(undefined); setImportOpen(false); setImportRows([]); setActionError(''); setListError(''); setImportError('');
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    getGpuServers(controller.signal).then(result => { if (!controller.signal.aborted) setServers(result); })
      .catch(cause => { if (!controller.signal.aborted) { if (forbidden(cause)) onForbidden(); else setListError(connectionError(cause, '服务器列表读取失败，请重试。')); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [admin, reload, onForbidden]);
  const saved = (server: GpuServer) => {
    setServers(list => list.some(item => item.id === server.id) ? list.map(item => item.id === server.id ? server : item) : [...list, server]);
    setEditing(undefined); setActionError(''); setNotice('服务器连接已保存。');
  };
  const change = async (server: GpuServer, action: 'enabled' | 'training_enabled' | 'remove') => {
    if (writing.current || server.source !== 'password') return;
    writing.current = true; setBusy(true); setActionError(''); setNotice('');
    try {
      if (action === 'remove') {
        await deleteGpuServer(server.id);
        if (mounted.current) { setServers(list => list.filter(item => item.id !== server.id)); setDeleting(undefined); setNotice('服务器连接已删除。'); }
      } else {
        const updated = await updateGpuServer(server.id, { [action]: !server[action] });
        if (mounted.current) setServers(list => list.map(item => item.id === server.id ? updated : item));
      }
    } catch (cause) {
      if (mounted.current) { if (forbidden(cause)) onForbidden(); else setActionError(connectionError(cause, action === 'remove' ? '连接删除失败，请重试。' : '服务器状态修改失败，请重试。')); }
    } finally { writing.current = false; if (mounted.current) setBusy(false); }
  };
  const readImport = async (file: File) => {
    setImportRows([]); setImportError('');
    if (file.size > 1024 ** 2) { setImportError('JSON 文件不能超过 1 MiB。'); return false; }
    setReading(true);
    try {
      const data = JSON.parse(await file.text());
      if (!data || typeof data !== 'object' || Array.isArray(data) || !Array.isArray(data.servers) || Object.keys(data).some(key => key !== 'servers')) throw Error();
      if (data.servers.length < 1 || data.servers.length > 200) { setImportError('servers 必须包含 1 至 200 条连接。'); return false; }
      if (data.servers.some((row: unknown) => !row || typeof row !== 'object' || Array.isArray(row))) throw Error();
      if (mounted.current) setImportRows(data.servers);
    } catch { if (mounted.current) setImportError('JSON 格式无效，请使用模板中的 {"servers":[...]} 结构。'); }
    finally { if (mounted.current) setReading(false); }
    return false;
  };
  const submitImport = async () => {
    if (writing.current || !importRows.length) return;
    writing.current = true; setBusy(true); setImportError(''); setNotice('');
    try {
      const result = await importGpuServers(importRows);
      if (mounted.current) {
        setServers(list => [...list, ...result.items]); setImportRows([]); setImportOpen(false); setNotice(`已新增 ${result.imported} 个服务器连接。`);
      }
    } catch (cause) {
      if (mounted.current) {
        if (forbidden(cause)) onForbidden();
        else setImportError(connectionError(cause, '导入失败，请检查字段或连接是否重复后重试。本批未新增任何连接。'));
      }
    } finally { writing.current = false; if (mounted.current) setBusy(false); }
  };
  if (!admin) return <Result status="403" title="无权限" subTitle="仅管理员可管理服务器连接。" />;
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {notice && <Alert type="success" showIcon closable onClose={() => setNotice('')} title={notice} />}
    {listError && <Alert type="error" showIcon title={listError} action={<Button onClick={() => { setLoading(true); setListError(''); setReload(value => value + 1); }}>重试列表</Button>} />}
    {actionError && !deleting && <Alert type="error" showIcon title={actionError} />}
    <Card title="服务器连接">
      <div className="page-toolbar">
        <Input.Search aria-label="搜索服务器" placeholder="搜索连接名称或IP" allowClear value={search} onChange={event=>{setSearch(event.target.value);setPage(1);}} style={{width:320,maxWidth:'100%'}} />
        <Space wrap><Button disabled={loading || busy} onClick={() => { setImportRows([]); setImportError(''); setImportOpen(true); }}>JSON 批量导入</Button><Button type="primary" disabled={loading || busy} onClick={() => { setActionError(''); setEditing(null); }}>新增服务器</Button></Space>
      </div>
      <Table<GpuServer> rowKey="id" loading={loading} dataSource={servers.filter(row=>!(row.id==='default'&&servers.some(other=>other.source==='password'&&other.name.toLowerCase()===row.name.toLowerCase()))).filter(row=>[row.name,row.host,row.jump?.host].some(value=>value?.toLowerCase().includes(search.trim().toLowerCase())))} pagination={{ current:page,pageSize:20,onChange:setPage }} scroll={{ x: 900 }} locale={{ emptyText: search.trim() ? '没有匹配的服务器' : '暂无服务器，请新增连接或导入 JSON 文件。' }} columns={[
        { title: '连接名称', dataIndex: 'name', render: value => <strong>{value}</strong> },
        { title: '来源', render: (_, row) => <Tag>{row.source === 'ssh_manager' ? 'ssh-manager · 只读' : '自建连接'}</Tag> },
        { title: '服务器', render: (_, row) => row.source === 'ssh_manager' ? '使用默认配置' : <><div style={{ overflowWrap: 'anywhere' }}>{row.username}@{row.host}</div><Typography.Text type="secondary">SSH 端口 {row.port}</Typography.Text></> },
        { title: '连接方式', render: (_, row) => row.jump ? <><div style={{ overflowWrap: 'anywhere' }}>经 {row.jump.username}@{row.jump.host}:{row.jump.port}</div><Tag color={row.jump.has_password ? undefined : 'warning'}>{row.jump.has_password ? '跳板机密码已设置' : '跳板机密码未设置'}</Tag></> : '直连 / 默认配置' },
        { title: '密码', render: (_, row) => row.source === 'ssh_manager' ? '由 ssh-manager 管理' : <Tag color={row.has_password ? 'success' : 'warning'}>{row.has_password ? '已设置密码' : '未设置密码'}</Tag> },
        { title: '启用采集', render: (_, row) => row.source === 'ssh_manager' ? <Tag>{row.enabled ? '已启用' : '已停用'}</Tag> : <Switch aria-label={`启用采集 ${row.name}`} checked={row.enabled} disabled={busy || editing !== undefined || !!deleting} onChange={() => { void change(row, 'enabled'); }} /> },
        { title: '训练服务器', render: (_, row) => row.source === 'ssh_manager' ? <Tag color="success">系统预设</Tag> : <Switch aria-label={`训练服务器 ${row.name}`} checked={row.training_enabled} disabled={busy || editing !== undefined || !!deleting} onChange={() => { void change(row, 'training_enabled'); }} /> },
        { title: '操作', render: (_, row) => <Space size="small" wrap><Link to={`/gpu-resources?server_id=${encodeURIComponent(row.id)}`}>查看资源</Link>{row.source === 'password' && <><Button size="small" disabled={busy} onClick={() => { setActionError(''); setEditing(row); }}>编辑连接</Button><Button size="small" danger disabled={busy} onClick={() => { setActionError(''); setDeleting(row); }}>删除连接</Button></>}</Space> },
      ]} />
    </Card>
    {editing !== undefined && <GpuServerForm server={editing || undefined} onClose={() => setEditing(undefined)} onSaved={saved} onForbidden={onForbidden} />}
    {deleting && <Modal open title="删除GPU服务器？" okText="确认删除" cancelText="取消" okButtonProps={{ danger: true, 'aria-label': '确认删除' }} confirmLoading={busy} mask={{ closable: false }} closable={!busy} cancelButtonProps={{ disabled: busy }} onCancel={() => { if (!busy) { setDeleting(undefined); setActionError(''); } }} onOk={() => { void change(deleting, 'remove'); }}>
      <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>删除连接“{deleting.name}”及其保存的密码？</Typography.Paragraph>{actionError && <Alert type="error" showIcon title={actionError} />}
    </Modal>}
    <Modal open={importOpen} title="JSON 批量导入服务器" width={700} okText={importRows.length ? `导入 ${importRows.length} 条连接` : '导入连接'} cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !importRows.length || reading }} cancelButtonProps={{ disabled: busy || reading }} closable={!busy && !reading} mask={{ closable: false }} onCancel={() => { if (!busy && !reading) { setImportOpen(false); setImportRows([]); setImportError(''); } }} onOk={() => { void submitImport(); }}>
      <Typography.Paragraph>上传 UTF-8 JSON 文件，结构为 <Typography.Text code>{'{"servers":[...]}'}</Typography.Text>，最多 200 条且不超过 1 MiB。整批仅新增，不覆盖现有连接；任一项无效或重复时全部不导入。</Typography.Paragraph>
      <Descriptions size="small" column={1} items={[
        { key: 'required', label: '必填字段', children: 'name（名称）、host（IPv4 / IPv6）、password（密码）' },
        { key: 'optional', label: '可选字段', children: 'port（默认22）、username（默认root）、enabled（默认true）、training_enabled（默认false）；jump 为可选跳板机对象，包含 host、password，以及可选 port、username。直连时省略或设为 null。' },
        { key: 'duplicate', label: '重复判断', children: '目标 IP、端口、用户名以及跳板机地址和账号均相同视为重复连接' },
      ]} />
      <Alert type="warning" showIcon title="JSON 文件包含明文密码" description="请妥善保管，导入后删除不再需要的文件。密码不会在列表或导入结果中回显；服务器配置保存不会执行 SSH 测试。" style={{ margin: '16px 0' }} />
      <Space wrap><Button onClick={downloadTemplate} disabled={busy}>下载 JSON 模板</Button><Upload accept=".json,application/json" showUploadList={false} beforeUpload={readImport} disabled={busy || reading}><Button icon={<UploadOutlined />} loading={reading} disabled={busy || reading}>选择 JSON 文件</Button></Upload></Space>
      {importRows.length > 0 && <Typography.Paragraph style={{ marginTop: 16 }}>已读取 {importRows.length} 条连接，可提交导入。</Typography.Paragraph>}
      {importError && <Alert type="error" role="alert" showIcon title={importError} style={{ marginTop: 16 }} />}
    </Modal>
  </Space>;
}
