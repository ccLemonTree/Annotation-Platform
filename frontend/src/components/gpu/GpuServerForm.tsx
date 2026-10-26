import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Modal, Space, Switch, Typography } from 'antd';
import { createGpuServer, testGpuServer, updateGpuServer } from '../../api/gpuResources';
import type { GpuResources, GpuServer, GpuServerInput } from '../../api/gpuResources';
import { connectionError, forbidden } from './connectionError';

export default function GpuServerForm({ server, onClose, onSaved, onForbidden }: {
  server?: GpuServer; onClose: () => void; onSaved: (server: GpuServer) => void; onForbidden: () => void;
}) {
  const [form] = Form.useForm<GpuServerInput>();
  const password = Form.useWatch('password', form), clear = Form.useWatch('clear_password', form);
  const [busy, setBusy] = useState<'test' | 'save'>(), [error, setError] = useState('');
  const [result, setResult] = useState<GpuResources>();
  const mounted = useRef(false), writing = useRef(false), request = useRef<AbortController | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; request.current?.abort(); form.resetFields(); };
  }, [form]);

  const input = (values: GpuServerInput): GpuServerInput => ({
    name: values.name.trim(), host: values.host.trim(), port: values.port, username: values.username.trim(), enabled: values.enabled,
    ...(values.password && !values.clear_password ? { password: values.password } : {}),
    ...(server ? { clear_password: !!values.clear_password } : {}),
  });
  const validate = async () => {
    try { return await form.validateFields(); } catch { return undefined; }
  };
  const test = async () => {
    if (writing.current) return;
    const values = await validate();
    if (!values?.password || values.clear_password || !mounted.current || writing.current) return;
    writing.current = true; setBusy('test'); setError(''); setResult(undefined);
    const controller = new AbortController(); request.current = controller;
    try {
      const { name, host, port, username, password } = input(values);
      const data = await testGpuServer({ name, host, port, username, password }, controller.signal);
      if (mounted.current && !controller.signal.aborted) setResult(data);
    } catch (cause) {
      if (mounted.current && !controller.signal.aborted) {
        if (forbidden(cause)) onForbidden(); else setError(connectionError(cause, '连接测试失败，请核对参数后重试。'));
      }
    } finally { writing.current = false; request.current = null; if (mounted.current) setBusy(undefined); }
  };
  const save = async () => {
    if (writing.current) return;
    const values = await validate();
    if (!values || !mounted.current || writing.current) return;
    writing.current = true; setBusy('save'); setError('');
    try {
      const saved = server ? await updateGpuServer(server.id, input(values)) : await createGpuServer(input(values));
      if (mounted.current) onSaved(saved);
    } catch (cause) {
      if (mounted.current) {
        if (forbidden(cause)) onForbidden(); else setError(connectionError(cause, '连接保存失败，请重试。'));
      }
    } finally { writing.current = false; if (mounted.current) setBusy(undefined); }
  };
  return <>
    <Modal open title={server ? '编辑GPU服务器' : '新增GPU服务器'} onCancel={() => { if (busy !== 'save') onClose(); }} closable={busy !== 'save'} mask={{ closable: false }}
      footer={<Space wrap>
        <Button onClick={onClose} disabled={busy === 'save'}>取消</Button>
        <Button aria-label="测试连接" onClick={() => { void test(); }} loading={busy === 'test'} disabled={!!busy || !password || !!clear}>测试连接</Button>
        <Button type="primary" aria-label="保存连接" loading={busy === 'save'} disabled={!!busy} onClick={() => { void save(); }}>保存连接</Button>
      </Space>}>
      <Form form={form} layout="vertical" disabled={!!busy} preserve={false}
        initialValues={{ name: server?.name || '', host: server?.host || '', port: server?.port ?? 22, username: server?.username || 'root', enabled: server?.enabled ?? true, clear_password: false }}
        onValuesChange={() => { setResult(undefined); setError(''); }}>
        <Typography.Paragraph type="secondary">支持 Linux NVIDIA 服务器，需已安装 Python3 和 nvidia-smi。可离线保存。</Typography.Paragraph>
        <Form.Item name="name" label="连接名称" rules={[{ required: true, whitespace: true, message: '请输入连接名称' }]}><Input maxLength={100} /></Form.Item>
        <Form.Item name="host" label="服务器IP" rules={[{ required: true, whitespace: true, message: '请输入IPv4或IPv6地址' }]}><Input placeholder="IPv4 / IPv6 地址" /></Form.Item>
        <Form.Item name="port" label="SSH端口" rules={[{ required: true, type: 'integer', min: 1, max: 65535, message: '端口须为1至65535的整数' }]}><InputNumber min={1} max={65535} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="username" label="用户名" rules={[{ required: true, pattern: /^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$/, message: '请输入有效的Linux用户名' }]}><Input autoComplete="off" maxLength={64} /></Form.Item>
        <Form.Item name="password" label="密码" dependencies={['host', 'port', 'username', 'clear_password']} extra={server ? '留空保留，重填替换；密码不会回显。' : undefined}
          rules={[({ getFieldValue }) => ({ validator(_, value) {
            const targetChanged = server && (getFieldValue('host')?.trim() !== server.host || getFieldValue('port') !== server.port || getFieldValue('username')?.trim() !== server.username);
            if (targetChanged && (!value || getFieldValue('clear_password'))) return Promise.reject(new Error('更改IP、端口或用户名时必须重填密码'));
            if (!server && !value) return Promise.reject(new Error('请输入密码'));
            return Promise.resolve();
          } })]}><Input.Password autoComplete="new-password" maxLength={1024} disabled={!!busy || !!clear} /></Form.Item>
        {server && <Form.Item name="clear_password" valuePropName="checked"><Checkbox onChange={e => { if (e.target.checked) form.setFieldValue('password', undefined); }}>清除已保存密码</Checkbox></Form.Item>}
        <Form.Item name="enabled" label="启用采集" valuePropName="checked"><Switch /></Form.Item>
      </Form>
      {server && !password && <Typography.Paragraph type="secondary">重填密码可测试未保存连接；已保存连接可在页面手动刷新。</Typography.Paragraph>}
      {clear && <Alert type="warning" title="将清除已保存密码，填写新密码后可继续采集。" />}
      {error && <Alert type="error" showIcon title={error} />}
      {result && <Alert type="success" showIcon title="未保存连接测试结果" description={<>
        <div>测试完成：{result.gpus.length} 张GPU，{result.processes.length} 个已采集计算进程。结果仅属于本次表单参数。</div>
        {!result.processes_complete && <div>进程采集不完整</div>}
        {result.warnings.map((warning, index) => <div key={index}>{warning}</div>)}
      </>} />}
    </Modal>
  </>;
}
