import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Modal, Popconfirm, Select, Space, Spin, Table, Tag, message } from 'antd';
import { isAxiosError } from 'axios';
import apiClient from '../api/client';

type Split = 'train' | 'val' | 'test';
type Selection = { mode: 'ratio' | 'train_only' | 'val_only'; ratios: Record<Split, number>; budgets: Record<string, number | null>; check_duplicates: boolean; group_similar: boolean; phash_distance: number; color_distance: number; require_source: boolean };
type Snapshot = { id: string; format: string; created_at: string; asset_count: number };
type Summary = { labels: string[]; all: { images: number; images_by_label: Record<string, number> }; approved: { images: number; images_by_label: Record<string, number> } };

const readable = (data: Record<string, unknown>) => Object.entries(data || {}).map(([key, value]) => `${key}: ${value}`).join(' · ');

async function failureReason(failure: unknown, fallback: string) {
  if (!isAxiosError(failure)) return fallback;
  let data = failure.response?.data;
  if (data instanceof Blob) {
    try { data = JSON.parse(await data.text()); } catch { return fallback; }
  }
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.message === 'string') return data.message;
  if (failure.code === 'ECONNABORTED' || failure.code === 'ETIMEDOUT') return '请求等待超时，服务端可能仍在处理。请先重新打开窗口检查冻结版本，避免重复提交。';
  if (!failure.response) return '网络连接中断，执行结果尚未确认。请先重新打开窗口检查冻结版本，避免重复提交。';
  return fallback;
}

export default function ProjectExport({ projectId }: { projectId: string }) {
  const [notice, context] = message.useMessage();
  const [open, setOpen] = useState(false), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [format, setFormat] = useState<'yolo' | 'coco'>('yolo');
  const [approved, setApproved] = useState(true), [summary, setSummary] = useState<Summary | null>(null);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false), [snapshotsError, setSnapshotsError] = useState('');
  const [labels, setLabels] = useState<string[]>([]), [selected, setSelected] = useState<string[]>([]), [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selection, setSelection] = useState<Selection>({ mode: 'ratio', ratios: { train: 80, val: 10, test: 10 }, budgets: {}, check_duplicates: false, group_similar: false, phash_distance: 4, color_distance: 20, require_source: false });
  const [report, setReport] = useState<Record<string, unknown> | null>(null), [elapsed, setElapsed] = useState(0);
  const grouping = report?.grouping as {group_count:number;largest_group:number;actual_ratios:Record<Split,number>;empty_requested_splits?:Split[]} | undefined;
  const eligible = summary?.[approved ? 'approved' : 'all'];

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => { setReport(null); }, [selected, approved, selection]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoaded(false); setSummary(null); setLabels([]); setSelected([]); setError(''); setReport(null);
    setSnapshots([]); setSnapshotsLoading(true); setSnapshotsError('');
    apiClient.get<Summary>(`/api/export/projects/${projectId}/summary`).then(({ data }) => {
      if (!active) return;
      setSummary(data); setLabels(data.labels); setSelected(data.labels); setLoaded(true);
    }).catch(() => { if (active) setError('导出数据读取失败，请重新打开'); });
    apiClient.get(`/api/export/projects/${projectId}/snapshots`, { params: { summary_only: true } }).then(({ data }) => {
      if (active) setSnapshots(data);
    }).catch(() => { if (active) setSnapshotsError('冻结版本读取失败，请重新打开'); })
      .finally(() => { if (active) setSnapshotsLoading(false); });
    return () => { active = false; };
  }, [open, projectId]);

  const request = () => ({ labels: selected, approved_only: approved, selection: { ...selection, budgets: Object.fromEntries(selected.map(label => [label, selection.budgets[label] ?? null])) } });
  const preview = async () => {
    if (!selected.length) return notice.error('至少选择一个标签');
    setBusy(true); setError(''); setReport(null); setElapsed(0);
    try { setReport((await apiClient.post(`/api/export/projects/${projectId}/splits/preview`, request(), { timeout: 300000 })).data); }
    catch (failure) { setError(await failureReason(failure, '划分预览失败，请检查标注和图片')); }
    finally { setBusy(false); }
  };
  const downloadBlob = async (responseUrl: string, body: Record<string, unknown>) => {
    const response = await apiClient.post(responseUrl, body, { responseType: 'blob', timeout: 600000 });
    const url = URL.createObjectURL(response.data); const link = document.createElement('a'); link.href = url; link.download = `detection_${projectId}_${format}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  };
  const run = async (action: 'download' | 'freeze' | 'freeze-download') => {
    if (!selected.length) return notice.error('至少选择一个标签');
    setBusy(true); setError('');
    let frozenId: string | undefined;
    try {
      const body = { format, ...request() };
      if (action === 'download') await downloadBlob(`/api/export/projects/${projectId}/packages`, body);
      else {
        const response = await apiClient.post(`/api/export/projects/${projectId}/snapshots`, body, { timeout: 600000 });
        frozenId = response.data.id;
        if (action === 'freeze-download') {
          const downloadResponse = await apiClient.get(`/api/export/snapshots/${response.data.id}/download`, { responseType: 'blob', timeout: 600000 });
          const url = URL.createObjectURL(downloadResponse.data); const link = document.createElement('a'); link.href = url; link.download = `snapshot_${response.data.id}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
        }
        setSnapshots((await apiClient.get(`/api/export/projects/${projectId}/snapshots`, { params: { summary_only: true } })).data); notice.success('冻结版本已保存');
      }
    } catch (failure) {
      const reason = await failureReason(failure, '导出失败，请刷新后检查冻结列表');
      setError(frozenId ? `冻结版本已创建（${frozenId}），但后续下载或列表刷新失败：${reason}` : reason);
    }
    finally { setBusy(false); }
  };
  const setRatio = (key: Split, value: number | null) => setSelection(previous => ({ ...previous, ratios: { ...previous.ratios, [key]: value ?? 0 } }));
  const removeSnapshot = async (id: string) => {
    setBusy(true);
    try {
      await apiClient.delete(`/api/export/snapshots/${id}`);
      setSnapshots(previous => previous.filter(snapshot => snapshot.id !== id));
      notice.success('冻结版本已删除');
    } catch (failure) {
      notice.error(isAxiosError(failure) && typeof failure.response?.data?.detail === 'string' ? failure.response.data.detail : '冻结版本删除失败，请重试');
    } finally { setBusy(false); }
  };
  return <>
    {context}<Button onClick={() => setOpen(true)}>导出 / 冻结数据集</Button>
    <Modal title="检测数据导出" open={open} width={960} onCancel={() => !busy && setOpen(false)} footer={null}>
      <Alert type="info" title="选择标签后按比例划分图片，并可限制每类训练图片数量。预览、下载和冻结使用同一套选择规则。" />
      {error && <Alert type="error" title={error} />}
      {busy && <Alert type="info" icon={<Spin size="small" />} title={`正在处理，已等待 ${elapsed} 秒`} />}
      <Space wrap style={{ margin: '16px 0' }}>
        <Select value={format} disabled={busy} onChange={setFormat} options={[{ value: 'yolo', label: 'YOLO ZIP' }, { value: 'coco', label: 'COCO ZIP' }]} />
        <Select mode="multiple" style={{ minWidth: 250 }} disabled={busy || !loaded} loading={!loaded && !error} value={selected} onChange={setSelected} options={labels.map(label => ({ label, value: label }))} placeholder={!loaded && !error ? '正在加载标签…' : '选择标签'} />
        <Checkbox checked={approved} disabled={busy} onChange={event => setApproved(event.target.checked)}>仅审核通过</Checkbox>
        <Button disabled={!loaded || busy} onClick={preview}>预览当前划分</Button>
        <Button disabled={!loaded || busy || !report || report.can_freeze === false} onClick={() => run('download')}>下载</Button>
        <Button type="primary" disabled={!loaded || busy || !report || report.can_freeze === false} onClick={() => run('freeze')}>仅冻结</Button>
        <Button disabled={!loaded || busy || !report || report.can_freeze === false} onClick={() => run('freeze-download')}>冻结并下载</Button>
      </Space>
      <Space wrap>
        <Select aria-label="划分方式" value={selection.mode} disabled={busy} onChange={(mode: Selection['mode']) => setSelection(previous => ({ ...previous, mode }))} options={[{ value: 'ratio', label: '按比例划分' }, { value: 'train_only', label: '仅训练集' }, { value: 'val_only', label: '仅验证集' }]} />
        {(['train', 'val', 'test'] as Split[]).map(key => <label key={key}>{key === 'train' ? '训练' : key === 'val' ? '验证' : '测试'} % <InputNumber min={0} max={100} precision={0} disabled={busy || selection.mode !== 'ratio'} value={selection.mode === 'ratio' ? selection.ratios[key] : key === (selection.mode === 'train_only' ? 'train' : 'val') ? 100 : 0} onChange={value => setRatio(key, value)} /></label>)}
      </Space>
      <p><Checkbox checked={selection.group_similar} disabled={busy} onChange={event => setSelection(previous => ({...previous,group_similar:event.target.checked,check_duplicates:event.target.checked || previous.check_duplicates}))}>相似图片自动分组后划分</Checkbox> 同组不拆分，实际比例允许偏差；每类训练预算按整组限制。</p>
      <p>当前范围 {eligible?.images ?? '加载中'} 张；每类预算仅限制训练图片，按图片计数。仅训练集仍受预算约束，整组超出预算时整组排除；仅验证集不受训练预算限制。</p>
      <Table size="small" pagination={false} rowKey="label" dataSource={selected.map(label => ({ label, available: eligible?.images_by_label[label] ?? 0, budget: selection.budgets[label] ?? null }))} columns={[{ title: '标签', dataIndex: 'label' }, { title: '可用图片', dataIndex: 'available' }, { title: '训练预算', render: (_: unknown, row: { label: string; budget?: number | null }) => <InputNumber min={1} precision={0} disabled={busy || selection.mode === 'val_only'} placeholder="不限" value={row.budget ?? undefined} onChange={value => setSelection(previous => ({ ...previous, budgets: { ...previous.budgets, [row.label]: value ?? null } }))} /> }, { title: '训练', render: (_: unknown, row: { label: string }) => (report?.labels_by_split as Record<string, Record<string, number>> | undefined)?.train?.[row.label] ?? '-' }, { title: '验证', render: (_: unknown, row: { label: string }) => (report?.labels_by_split as Record<string, Record<string, number>> | undefined)?.val?.[row.label] ?? '-' }, { title: '测试', render: (_: unknown, row: { label: string }) => (report?.labels_by_split as Record<string, Record<string, number>> | undefined)?.test?.[row.label] ?? '-' }]} />
      {report && <Alert type="info" title={`划分结果：${readable(report.images_by_split as Record<string, unknown>)}；排除 ${((report.excluded_asset_ids as string[]) || []).length} 张`} />}
      {typeof report?.failure_reason === 'string' && report.failure_reason && <Alert type="error" showIcon title={report.failure_reason} />}
      {grouping && <Alert type="info" showIcon title={`分组结果：${grouping.group_count} 组，最大 ${grouping.largest_group} 张；实际比例：训练 ${grouping.actual_ratios.train}% · 验证 ${grouping.actual_ratios.val}% · 测试 ${grouping.actual_ratios.test}%`} />}
      {!!grouping?.empty_requested_splits?.length && <Alert type="warning" showIcon title={`整组划分后 ${grouping.empty_requested_splits.map(key => ({train:'训练集',val:'验证集',test:'测试集'})[key]).join('、')} 为空；请调整比例或补充独立图片组，同组图片不会拆开。`} />}
      <details><summary>高级：相似图片规则与防泄漏检查</summary><Space wrap style={{ marginTop: 10 }}><Checkbox checked={selection.check_duplicates} disabled={busy || selection.group_similar} onChange={event => setSelection(previous => ({ ...previous, check_duplicates: event.target.checked }))}>启用来源分组、pHash、颜色阈值检查</Checkbox><label>pHash <InputNumber min={0} max={64} value={selection.phash_distance} disabled={busy || !selection.check_duplicates} onChange={value => setSelection(previous => ({ ...previous, phash_distance: value ?? 4 }))} /></label><label>颜色 <InputNumber min={0} max={255} value={selection.color_distance} disabled={busy || !selection.check_duplicates} onChange={value => setSelection(previous => ({ ...previous, color_distance: value ?? 20 }))} /></label><Checkbox checked={selection.require_source} disabled={busy || !selection.check_duplicates} onChange={event => setSelection(previous => ({ ...previous, require_source: event.target.checked }))}>来源缺失禁止冻结</Checkbox></Space></details>
      <h3>冻结版本</h3>
      {snapshotsLoading && <Space><Spin size="small" />正在加载冻结版本…</Space>}
      {snapshotsError && <Alert type="error" title={snapshotsError} />}
      {!snapshotsLoading && !snapshotsError && !snapshots.length && <p>暂无冻结版本</p>}
      {snapshots.map(snapshot => <Space key={snapshot.id} style={{ display: 'flex', marginBottom: 8 }}><Button disabled={busy} onClick={() => apiClient.get(`/api/export/snapshots/${snapshot.id}/download`, { responseType: 'blob', timeout: 600000 }).then(response => { const url = URL.createObjectURL(response.data); const link = document.createElement('a'); link.href = url; link.download = `snapshot_${snapshot.id}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); }).catch(async failure => setError(await failureReason(failure, '冻结版本下载失败，请重试')))}>下载 {snapshot.format} · {snapshot.asset_count} 张</Button><Tag>{new Date(snapshot.created_at).toLocaleString()}</Tag><Popconfirm title="删除此冻结版本？" description="删除冻结包和版本记录，原图与项目标注保留。被训练任务或验证集引用的版本不能删除。" okText="删除" cancelText="取消" onConfirm={() => removeSnapshot(snapshot.id)}><Button danger aria-label={`删除冻结版本 ${snapshot.id}`} disabled={busy}>删除</Button></Popconfirm></Space>)}
    </Modal>
  </>;
}
