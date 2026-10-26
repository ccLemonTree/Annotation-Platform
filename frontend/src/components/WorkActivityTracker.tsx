import { useEffect, useState } from 'react';
import { Segmented, Space, Typography } from 'antd';
import { postActivity } from '../api/analytics';
import type { ActivityHeartbeat, WorkActivity } from '../api/analytics';

interface TrackingContext { projectId: string; assetId: string; activity: WorkActivity }

function startActivityTracking(context: TrackingContext, status: (value: string) => void) {
  // getRandomValues remains available on LAN HTTP, where randomUUID is unavailable.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  const sessionId = [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  let sequence = 0, cursor: number | null = null, lastInput = 0, sending = false;
  const pending: ActivityHeartbeat[] = [];
  const eligible = () => document.visibilityState === 'visible' && document.hasFocus();
  const drain = async () => {
    if (sending) return;
    sending = true;
    try {
      while (pending.length) {
        if (Date.now()-Date.parse(pending[0].ended_at) > 120000) {
          pending.shift(); status('部分计时未保存（离线超过2分钟）'); continue;
        }
        await postActivity(pending[0]); pending.shift();
        status('活跃计时已保存；空闲60秒暂停');
      }
    } catch { status('计时保存失败，正在重试；请保持页面打开'); }
    finally { sending = false; }
  };
  const flush = () => {
    if (cursor !== null) {
      const end = Math.min(Date.now(), lastInput+60000);
      if (end > cursor) {
        pending.push({ project_id: context.projectId, asset_id: context.assetId,
          session_id: sessionId, sequence: ++sequence, activity: context.activity,
          started_at: new Date(Math.max(cursor, end-30000)).toISOString(), ended_at: new Date(end).toISOString() });
      }
      cursor = end;
      if (Date.now() >= lastInput+60000) { cursor = null; status('空闲已暂停；操作后恢复'); }
    }
    void drain();
  };
  const input = (event: Event) => {
    if (!event.isTrusted || !eligible()) return;
    const now = Date.now();
    if (cursor !== null && now >= lastInput+60000) flush();
    if (cursor === null) { cursor = now; status('正在采集活跃时间'); }
    lastInput = now;
  };
  const pause = () => { flush(); cursor = null; status('计时已暂停；返回后操作恢复'); };
  const visibility = () => { if (!eligible()) pause(); };
  const events = ['pointerdown', 'pointermove', 'keydown', 'wheel'];
  // Capture before canvas shortcuts stop event propagation.
  events.forEach(name => window.addEventListener(name, input, { passive: true, capture: true }));
  window.addEventListener('blur', pause);
  window.addEventListener('pagehide', pause);
  document.addEventListener('visibilitychange', visibility);
  const timer = window.setInterval(() => { if (eligible()) flush(); else pause(); }, 15000);
  status('等待工作台操作后计时；空闲60秒暂停');
  return () => {
    window.clearInterval(timer);
    events.forEach(name => window.removeEventListener(name, input, true));
    window.removeEventListener('blur', pause);
    window.removeEventListener('pagehide', pause);
    document.removeEventListener('visibilitychange', visibility);
    flush();
  };
}

export default function WorkActivityTracker({ projectId, assetId, enabled, initialActivity = 'annotation' }: {
  projectId: string; assetId: string; enabled: boolean; initialActivity?: WorkActivity;
}) {
  const [activity, setActivity] = useState<WorkActivity>(initialActivity);
  const [status, setStatus] = useState('等待工作台操作后计时');
  useEffect(() => {
    if (!enabled) return;
    let mounted = true;
    const stop = startActivityTracking({ projectId, assetId, activity }, value => { if (mounted) setStatus(value); });
    return () => { mounted = false; stop(); };
  }, [projectId, assetId, enabled, activity]);
  return <Space wrap style={{ marginBottom: 12 }}>
    <Segmented aria-label="工作计时模式" value={activity} disabled={!enabled}
      options={[{ label: '标注计时', value: 'annotation' }, { label: '审核计时', value: 'review' }]}
      onChange={value => setActivity(value as WorkActivity)} />
    <Typography.Text type="secondary" role="status">{enabled ? status : '计时暂停（历史只读或工作台未就绪）'}</Typography.Text>
  </Space>;
}
