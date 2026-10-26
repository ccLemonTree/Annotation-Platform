import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Result, Select, Space, Tag, Typography } from 'antd';
import { Link,useSearchParams } from 'react-router-dom';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import './GpuTerminalPage.css';
import { API_BASE_URL } from '../api/client';
import { getGpuServers } from '../api/gpuResources';
import type { GpuServer } from '../api/gpuResources';
import { connectionError, forbidden } from '../components/gpu/connectionError';

type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'closing' | 'error';
const stateLabels = { disconnected: '未连接', connecting: '正在连接', connected: '已连接', closing: '正在断开', error: '连接失败' };

export default function GpuTerminalPage() {
  const [params] = useSearchParams();
  const [admin, setAdmin] = useState(() => { try { return JSON.parse(localStorage.getItem('user') || 'null')?.role === 'admin'; } catch { return false; } });
  const [servers, setServers] = useState<GpuServer[]>([]), [serverId, setServerId] = useState(params.get('server_id') || 'default');
  const [loading, setLoading] = useState(true), [reload, setReload] = useState(0), [listError, setListError] = useState('');
  const [state, setState] = useState<ConnectionState>('disconnected'), [error, setError] = useState('');
  const surface = useRef<HTMLDivElement>(null), terminal = useRef<Terminal | null>(null), fit = useRef<FitAddon | null>(null);
  const socket = useRef<WebSocket | null>(null), ready = useRef(false), queuedInput = useRef<number[]>([]), flushTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const selected = servers.find(server => server.id === serverId);
  const busy = state === 'connecting' || state === 'connected' || state === 'closing';
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    getGpuServers(controller.signal).then(list => {
      if (controller.signal.aborted) return;
      setServers(list); setServerId(id => list.some(server => server.id === id) ? id : list[0]?.id || 'default');
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (forbidden(cause)) setAdmin(false); else setListError(connectionError(cause, '服务器列表读取失败'));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [admin, reload]);
  useEffect(() => {
    if (!admin || !surface.current) return;
    const term = new Terminal({ cursorBlink: true, disableStdin: true, scrollback: 3000, fontSize: 14,
      fontFamily: 'Consolas, "SFMono-Regular", monospace', theme: { background: '#111827', foreground: '#e5e7eb', cursor: '#93c5fd' } });
    const addon = new FitAddon(); term.loadAddon(addon); term.open(surface.current); addon.fit();
    term.textarea?.setAttribute('aria-label', 'SSH终端输入');
    term.writeln('选择GPU服务器，点击“连接终端”开始。');
    terminal.current = term; fit.current = addon;
    // A server's escape sequences must not read or write the local clipboard.
    const clipboardBlock = term.parser.registerOscHandler(52, () => true);
    const flush = () => {
      flushTimer.current = undefined;
      const ws = socket.current;
      if (!ready.current || !ws || ws.readyState !== WebSocket.OPEN) { queuedInput.current = []; return; }
      if (ws.bufferedAmount < 65536) while (queuedInput.current.length) ws.send(new Uint8Array(queuedInput.current.splice(0, 8192)));
      if (queuedInput.current.length) flushTimer.current = setTimeout(flush, 20);
    };
    const sendInput = (bytes: Uint8Array) => {
      if (!ready.current) return;
      if (bytes.length + queuedInput.current.length > 65536) { setError('输入过大或网络繁忙，请分段粘贴（最多64KiB）。'); return; }
      queuedInput.current.push(...bytes);
      if (!flushTimer.current) flushTimer.current = setTimeout(flush, 20);
    };
    const input = term.onData(data => sendInput(new TextEncoder().encode(data)));
    const binary = term.onBinary(data => sendInput(Uint8Array.from(data, value => value.charCodeAt(0) & 255)));
    const resize = term.onResize(({ cols, rows }) => {
      if (ready.current && socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type: 'resize', cols, rows }));
    });
    const observer = new ResizeObserver(() => {
      addon.fit();
      if (term.cols > 400 || term.rows > 200) term.resize(Math.min(400, term.cols), Math.min(200, term.rows));
    });
    observer.observe(surface.current);
    return () => {
      ready.current = false; clearTimeout(flushTimer.current); queuedInput.current = [];
      const ws = socket.current; socket.current = null; ws?.close();
      observer.disconnect(); input.dispose(); binary.dispose(); resize.dispose(); clipboardBlock.dispose(); term.dispose();
      terminal.current = null; fit.current = null;
    };
  }, [admin]);
  const connect = () => {
    if (socket.current || !selected || !terminal.current) return;
    const token = localStorage.getItem('token');
    if (!token) { setError('登录已过期，请重新登录。'); return; }
    setError(''); setState('connecting'); ready.current = false;
    terminal.current.reset(); fit.current?.fit();
    const url = new URL('/api/gpu-terminal/ws', new URL(API_BASE_URL || window.location.origin, window.location.origin));
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(url); ws.binaryType = 'arraybuffer'; socket.current = ws;
    ws.onopen = () => {
      if (socket.current !== ws) return;
      ws.send(JSON.stringify({ type: 'auth', token, server_id: selected.id, cols: terminal.current?.cols || 80, rows: terminal.current?.rows || 24 }));
    };
    ws.onmessage = event => {
      if (socket.current !== ws || !terminal.current) return;
      if (event.data instanceof ArrayBuffer) {
        const data = new Uint8Array(event.data);
        terminal.current.write(data, () => { if (socket.current === ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ack', bytes: data.length })); });
        return;
      }
      try {
        const value = JSON.parse(event.data);
        if (value.type === 'ready') {
          ready.current = true; terminal.current.options.disableStdin = false; setState('connected');
          fit.current?.fit(); terminal.current.focus();
          ws.send(JSON.stringify({ type: 'resize', cols: terminal.current.cols, rows: terminal.current.rows }));
        } else if (value.type === 'error') {
          setError(value.message || 'SSH终端连接失败'); setState('error');
          if (value.code === 403) setAdmin(false);
        }
      } catch { setError('终端返回无效数据'); ws.close(); }
    };
    ws.onerror = () => { if (socket.current === ws) { setError('终端连接失败，请检查网络或服务器连接。'); setState('error'); } };
    ws.onclose = () => {
      if (socket.current !== ws) return;
      socket.current = null; ready.current = false; clearTimeout(flushTimer.current); queuedInput.current = [];
      if (terminal.current) terminal.current.options.disableStdin = true;
      setState(previous => previous === 'error' ? 'error' : 'disconnected');
    };
  };
  const disconnect = () => {
    ready.current = false;
    if (terminal.current) terminal.current.options.disableStdin = true;
    setState('closing'); socket.current?.close(1000, 'user_disconnect');
  };
  if (!admin) return <Result status="403" title="无权限" subTitle="仅管理员可使用GPU服务器SSH终端。" />;
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    <Alert type="info" showIcon title="完整SSH交互终端" description="命令以所选服务器的SSH账号权限执行，支持top、vim、Tab补全和Ctrl+C。刷新或离开页面会断开当前shell；长任务请使用tmux或nohup。" />
    <Card title="服务器与会话" extra={<Tag color={state === 'connected' ? 'success' : state === 'error' ? 'error' : busy ? 'processing' : 'default'}>{stateLabels[state]}</Tag>}>
      <Space wrap>
        <Select aria-label="SSH调试服务器" value={selected?.id} loading={loading} disabled={busy} style={{ width: 280, maxWidth: '100%' }} placeholder="选择已保存的GPU服务器"
          options={servers.map(server => ({ value: server.id, label: server.name, disabled: !server.enabled || server.source === 'password' && !server.has_password }))}
          onChange={id => { setServerId(id); setError(''); terminal.current?.reset(); }} />
        <Button type="primary" loading={state === 'connecting'} disabled={loading || busy || !selected || !selected.enabled || selected.source === 'password' && !selected.has_password} onClick={connect}>连接终端</Button>
        <Button disabled={!busy || state === 'closing'} onClick={disconnect}>断开连接</Button>
        <Button disabled={state !== 'connected'} onClick={() => { socket.current?.send(new Uint8Array([3])); terminal.current?.focus(); }}>发送 Ctrl+C</Button>
        <Button onClick={() => { terminal.current?.clear(); terminal.current?.focus(); }}>清屏</Button>
        {selected&&<Link to={`/gpu-resources?server_id=${encodeURIComponent(selected.id)}`}>查看此服务器GPU</Link>}
      </Space>
      {selected && <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, overflowWrap: 'anywhere' }}>{selected.source === 'password' ? `${selected.username}@${selected.host}:${selected.port}` : `${selected.name} · 使用已有ssh-manager配置`} · 一页一个会话，空闲20分钟断开</Typography.Paragraph>}
      {listError && <Alert style={{ marginTop: 12 }} type="error" title={listError} action={<Button onClick={() => { setLoading(true); setListError(''); setReload(value => value + 1); }}>重试</Button>} />}
      {error && <Alert style={{ marginTop: 12 }} type="error" showIcon title={error} />}
    </Card>
    <div className="gpu-terminal-surface" role="region" aria-label="SSH交互终端"><div className="gpu-terminal-viewport" ref={surface} /></div>
  </Space>;
}
