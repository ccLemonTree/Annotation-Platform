import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Modal, Space, Table, Typography } from 'antd';
import { confirmGpuRelease, getGpuReleaseTasks, previewGpuRelease } from '../../api/gpuResources';
import type { GpuReleasePreview, GpuReleaseTarget, GpuReleaseTask } from '../../api/gpuResources';
import { connectionError, forbidden } from './connectionError';
import GpuSnapshot from './GpuSnapshot';

export default function GpuReleaseWorkspace({ serverId, onForbidden }: { serverId: string; onForbidden: () => void }) {
  const [tasks, setTasks] = useState<GpuReleaseTask[]>([]), [reload, setReload] = useState(0);
  const [preview, setPreview] = useState<GpuReleasePreview>(), [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [taskError, setTaskError] = useState('');
  const [notice, setNotice] = useState<{ stopped: boolean; message: string }>();
  const active = useRef(false), writing = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    getGpuReleaseTasks(serverId, controller.signal).then(rows => { if (!controller.signal.aborted) { setTasks(rows); setTaskError(''); } })
      .catch(cause => { if (!controller.signal.aborted) { if (forbidden(cause)) onForbidden(); else setTaskError(connectionError(cause, '平台任务占用读取失败，请刷新重试')); } });
    return () => controller.abort();
  }, [serverId, reload, onForbidden]);
  const inspect = async (target: GpuReleaseTarget) => {
    if (writing.current) return;
    writing.current = true; setBusy(true); setError(''); setNotice(undefined);
    try { const result = await previewGpuRelease(serverId, target); if (active.current) { setPreview(result); setForce(false); } }
    catch (cause) { if (active.current) { if (forbidden(cause)) onForbidden(); else setError(connectionError(cause, '目标核对失败，请刷新后重试')); } }
    finally { writing.current = false; if (active.current) setBusy(false); }
  };
  const confirm = async () => {
    if (writing.current || !preview) return;
    writing.current = true; setBusy(true); setError('');
    try {
      const result = await confirmGpuRelease(serverId, preview.token, force);
      if (active.current) { setNotice(result); setPreview(undefined); setReload(value => value + 1); }
    } catch (cause) {
      if (active.current) { if (forbidden(cause)) onForbidden(); else { setError(connectionError(cause, '停止未确认完成，请核对远端状态后重试')); setReload(value => value + 1); } }
    } finally { writing.current = false; if (active.current) setBusy(false); }
  };
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    {notice && <Alert type={notice.stopped ? 'success' : 'warning'} showIcon title={notice.message} />}
    {error && !preview && <Alert type="error" showIcon title={error} />}
    <Card title="平台GPU任务与资源预留" extra={<Button disabled={busy} onClick={() => setReload(value => value + 1)}>刷新占用</Button>}>
      <Typography.Paragraph type="secondary">停止所选任务会影响它使用的全部GPU。远端停止确认后解除平台预留；其他进程仍可能占用显存。</Typography.Paragraph>
      {taskError && <Alert type="warning" showIcon title={taskError} style={{ marginBottom: 12 }} />}
      <Table<GpuReleaseTask> rowKey={task => `${task.kind}:${task.id}`} dataSource={tasks} scroll={{ x: 700 }} pagination={{ pageSize: 10 }}
        locale={{ emptyText: taskError ? '任务占用未知' : '无平台GPU任务预留' }} columns={[
          { title: '任务名称', dataIndex: 'name' },
          { title: '类型', dataIndex: 'kind', render: kind => kind === 'training' ? '模型训练' : '验证 / 效果预览' },
          { title: '状态', dataIndex: 'status', render: (status: string) => ({ running: '运行中', starting: '启动中', stopping: '停止中', unknown: '待核对', failed: '失败', completed: '已完成', canceled: '已取消' }[status] || status) },
          { title: 'GPU', dataIndex: 'gpu_uuids', render: uuids => <span style={{ overflowWrap: 'anywhere' }}>{uuids.join('、')}</span> },
          { title: '操作', key: 'actions', width: 140, fixed: 'right', render: (_, task) => <Button danger disabled={busy} aria-label={`释放任务 ${task.name}`} onClick={() => { void inspect({ task_kind: task.kind, task_id: task.id }); }}>停止并释放</Button> },
        ]} />
    </Card>
    <GpuSnapshot serverId={serverId} onForbidden={onForbidden} refreshKey={reload} releaseBusy={busy}
      onReleaseProcess={process => { void inspect({ pid: process.pid, gpu_uuid: process.gpu_uuid }); }} />
    {preview && <Modal open title="确认释放GPU资源" okText="确认停止并释放" cancelText="取消" confirmLoading={busy}
      okButtonProps={{ danger: true, 'aria-label': '确认停止并释放' }} cancelButtonProps={{ disabled: busy }} mask={{ closable: false }} closable={!busy}
      onCancel={() => { if (!busy) { setPreview(undefined); setError(''); } }} onOk={() => { void confirm(); }}>
      <Typography.Paragraph>服务器：{preview.server_name}</Typography.Paragraph>
      <Typography.Paragraph>目标：<strong>{preview.target.name}</strong>{preview.target.pid && `（PID ${preview.target.pid}）`}</Typography.Paragraph>
      <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>影响GPU：{preview.target.gpu_uuids.join('、')}</Typography.Paragraph>
      <Alert type="warning" showIcon title={preview.target.kind !== 'process' ? '将停止整个任务，任务已保存的文件和权重保留。' : preview.target.container ? `将停止整个容器 ${preview.target.container.name}，其中其他进程也会停止。` : '将结束所选进程，未保存的计算结果可能丢失。'} />
      {preview.target.kind === 'process' && !preview.target.container && <Checkbox checked={force} disabled={busy} onChange={event => setForce(event.target.checked)} style={{ marginTop: 16 }}>正常结束超时后强制结束进程</Checkbox>}
      {error && <Alert type="error" showIcon title={error} style={{ marginTop: 16 }} />}
    </Modal>}
  </Space>;
}
