import { useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Checkbox, Input, InputNumber, Modal, Select, Space, Spin, Table } from 'antd';
import { isAxiosError } from 'axios';
import apiClient from '../api/client';

type Split = 'train' | 'val' | 'test';
type Selection = { mode: 'ratio' | 'none'; ratios: Record<Split, number>; budgets: Record<string, number | null>; check_duplicates: boolean; group_similar: boolean; phash_distance: number; color_distance: number; require_source: boolean };
type Summary = { labels: string[]; all: { images: number; images_by_label: Record<string, number> }; approved: { images: number; images_by_label: Record<string, number> } };
export type ExportScope = {work_job_id?:string;image_kind:'all'|'originals'|'crops'};

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

export default function ProjectExport({ projectId, scope, onFrozen }: { projectId: string; scope: ExportScope; onFrozen: (downloadJobId?: string) => void }) {
  const { message: notice } = App.useApp();
  const [open, setOpen] = useState(false), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const [error, setError] = useState(''), [format, setFormat] = useState<'yolo' | 'coco'>('yolo');
  const [approved, setApproved] = useState(true), [summary, setSummary] = useState<Summary | null>(null);
  const [versionLabel,setVersionLabel]=useState('');
  const [labels, setLabels] = useState<string[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [selection, setSelection] = useState<Selection>({ mode: 'ratio', ratios: { train: 80, val: 10, test: 10 }, budgets: {}, check_duplicates: false, group_similar: false, phash_distance: 4, color_distance: 20, require_source: false });
  const [report, setReport] = useState<Record<string, unknown> | null>(null), [elapsed, setElapsed] = useState(0);
  const grouping = report?.grouping as {group_count:number;largest_group:number;actual_ratios:Record<Split,number>;empty_requested_splits?:Split[]} | undefined;
  const eligible = summary?.[approved ? 'approved' : 'all'];
  const splitEnabled=selection.mode==='ratio';
  const checkingSimilarImages=selection.group_similar||selection.check_duplicates;
  const packageTimeout = splitEnabled&&selection.group_similar ? 1800000 : 600000;
  const splitModeId=`export-split-mode-${projectId}`;
  const counts=report?.images_by_split as Partial<Record<Split,number>>|undefined;
  const missingTrainingSplits=(['train','val'] as const).filter(split=>!counts?.[split]);
  const pending=(report?.review_counts as {pending?:number}|undefined)?.pending || 0;

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
    apiClient.get<Summary>(`/api/export/projects/${projectId}/summary`,{params:scope}).then(({ data }) => {
      if (!active) return;
      setSummary(data); setLabels(data.labels); setSelected(data.labels); setLoaded(true);
    }).catch(() => { if (active) setError('导出数据读取失败，请重新打开'); });
    return () => { active = false; abortRef.current?.abort(); };
  }, [open, projectId, scope.work_job_id, scope.image_kind]);

  const cancelTask = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };
  const request = () => ({ ...scope, labels: selected, approved_only: approved, selection: { ...selection, budgets: Object.fromEntries(selected.map(label => [label, selection.budgets[label] ?? null])) } });
  const preview = async () => {
    if (!selected.length) return notice.error('至少选择一个标签');
    cancelTask();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true); setError(''); setReport(null); setElapsed(0);
    try {
      setReport((await apiClient.post(`/api/export/projects/${projectId}/splits/preview`, request(), {
        timeout: checkingSimilarImages ? 0 : 300000,
        skipErrorToast: true,
        signal: controller.signal,
      })).data);
    } catch (failure) {
      if (controller.signal.aborted || (isAxiosError(failure) && failure.code === 'ERR_CANCELED')) {
        setError('');
        notice.info('已取消划分预览');
        return;
      }
      setError(isAxiosError(failure) && !failure.response
        ? '划分预览未返回结果，请检查连接后重新预览。'
        : await failureReason(failure, '划分预览失败，请检查标注和图片'));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  };
  const downloadBlob = async (responseUrl: string, body: Record<string, unknown>, signal: AbortSignal) => {
    const response = await apiClient.post(responseUrl, body, { responseType: 'blob', timeout: packageTimeout, signal });
    const url = URL.createObjectURL(response.data); const link = document.createElement('a'); link.href = url; link.download = `detection_${projectId}_${format}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  };
  const run = async (action: 'download' | 'freeze' | 'freeze-download') => {
    if (!selected.length) return notice.error('至少选择一个标签');
    cancelTask();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true); setError('');
    try {
      const body = { format, ...request(), preview_token: report?.preview_token };
      if (action === 'download') await downloadBlob(`/api/export/projects/${projectId}/packages`, body, controller.signal);
      else {
        const { data } = await apiClient.post<{ id: string }>(`/api/export/projects/${projectId}/snapshots/jobs`,
          { ...body, version_label: versionLabel }, { skipErrorToast: true, signal: controller.signal });
        onFrozen(action === 'freeze-download' ? data.id : undefined);
        setOpen(false);
      }
    } catch (failure) {
      if (controller.signal.aborted || (isAxiosError(failure) && failure.code === 'ERR_CANCELED')) {
        setError('');
        notice.info('已取消当前导出请求');
        return;
      }
      if (isAxiosError(failure) && failure.response?.status === 409) setReport(null);
      const reason = await failureReason(failure, '导出失败，请刷新后检查冻结列表');
      setError(reason);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  };
  const setRatio = (key: Split, value: number | null) => setSelection(previous => ({ ...previous, ratios: { ...previous.ratios, [key]: value ?? 0 } }));
  return <>
    <Button onClick={() => setOpen(true)}>导出 / 冻结数据集</Button>
    <Modal title="检测数据导出" open={open} width={960} styles={{ body: { maxHeight: '75dvh', overflowY: 'auto' } }} onCancel={() => { cancelTask(); setOpen(false); }} footer={null}>
      <Alert type="info" showIcon title="取消勾选「仅审核通过」可纳入待审核标注用于训练。已驳回及其他被排除的图片不会打包。" />
      {error && <Alert type="error" showIcon title={error} style={{ marginTop: 12 }} />}
      {busy && <Alert type="info" icon={<Spin size="small" />} showIcon style={{ marginTop: 12 }} title={`正在处理，已等待 ${elapsed} 秒`}
        action={<Button size="small" danger onClick={cancelTask}>取消</Button>} />}
      <Space wrap style={{ margin: '16px 0' }}>
        <Space orientation="vertical" size={4}><label htmlFor={`export-format-${projectId}`}>导出格式</label><Select id={`export-format-${projectId}`} aria-label="导出格式" value={format} disabled={busy} onChange={setFormat} options={[{ value: 'yolo', label: 'YOLO ZIP' }, { value: 'coco', label: 'COCO ZIP' }]} /></Space>
        <Space orientation="vertical" size={4}><label htmlFor={`export-labels-${projectId}`}>导出类别</label><Select id={`export-labels-${projectId}`} aria-label="导出类别" mode="multiple" style={{ minWidth: 250 }} disabled={busy || !loaded} loading={!loaded && !error} value={selected} onChange={setSelected} options={labels.map(label => ({ label, value: label }))} placeholder={!loaded && !error ? '正在加载标签…' : '选择标签'} /></Space>
        <Checkbox checked={approved} disabled={busy} onChange={event => setApproved(event.target.checked)}>仅审核通过</Checkbox>
        <Button disabled={!loaded || busy} onClick={preview}>预览当前划分</Button>
        <Button disabled={!loaded || busy || !report || report.can_freeze === false} onClick={() => run('download')}>下载</Button>
        <Button type="primary" disabled={!loaded || busy || !report || report.can_freeze === false} onClick={() => run('freeze')}>仅冻结</Button>
        <Button disabled={!loaded || busy || !report || report.can_freeze === false} onClick={() => run('freeze-download')}>冻结并下载</Button>
      </Space>
      <Space wrap>
        <label htmlFor={splitModeId}>划分方式</label><Select id={splitModeId} aria-label="划分方式" value={selection.mode} disabled={busy} onChange={(mode: Selection['mode']) => setSelection(previous => ({ ...previous, mode }))} options={[{ value: 'ratio', label: '按比例划分' }, { value: 'none', label: '不划分' }]} />
        {splitEnabled&&(['train', 'val', 'test'] as Split[]).map(key => <label key={key}>{key === 'train' ? '训练' : key === 'val' ? '验证' : '测试'} % <InputNumber aria-label={`${key==='train'?'训练集':key==='val'?'验证集':'测试集'}比例（%）`} min={0} max={100} precision={0} disabled={busy} value={selection.ratios[key]} onChange={value => setRatio(key, value)} /></label>)}
      </Space>
      {splitEnabled&&<p><Checkbox checked={selection.group_similar} disabled={busy} onChange={event => setSelection(previous => ({...previous,group_similar:event.target.checked}))}>相似图片自动分组后划分</Checkbox> 同组不拆分，实际比例允许偏差。</p>}
      <p>当前范围 {eligible?.images ?? '加载中'} 张。{splitEnabled?'每类预算仅限制训练图片，按图片计数。分组划分时按整组限制。':'符合审核和类别范围的图片统一打包。'}</p>
      <Table size="small" scroll={{x:700}} pagination={false} rowKey="label" dataSource={selected.map(label => ({ label, available: eligible?.images_by_label[label] ?? 0, budget: selection.budgets[label] ?? null }))} columns={[{ title: '标签', dataIndex: 'label' }, { title: '可用图片', dataIndex: 'available' }, { title: '每类训练图片预算', hidden: !splitEnabled, render: (_: unknown, row: { label: string; budget?: number | null }) => <InputNumber aria-label={`${row.label} 每类训练图片预算`} min={1} precision={0} disabled={busy} placeholder="不限" value={row.budget ?? undefined} onChange={value => setSelection(previous => ({ ...previous, budgets: { ...previous.budgets, [row.label]: value ?? null } }))} /> }, { title: '训练', hidden: !splitEnabled, render: (_: unknown, row: { label: string }) => (report?.labels_by_split as Record<string, Record<string, number>> | undefined)?.train?.[row.label] ?? '-' }, { title: '验证', hidden: !splitEnabled, render: (_: unknown, row: { label: string }) => (report?.labels_by_split as Record<string, Record<string, number>> | undefined)?.val?.[row.label] ?? '-' }, { title: '测试', hidden: !splitEnabled, render: (_: unknown, row: { label: string }) => (report?.labels_by_split as Record<string, Record<string, number>> | undefined)?.test?.[row.label] ?? '-' }]} />
      {report && <Alert type="info" title={`${splitEnabled?`划分结果：${readable(report.images_by_split as Record<string, unknown>)}`:`不划分：共 ${(report.images_by_split as {all?:number})?.all??0} 张`}；排除 ${((report.excluded_asset_ids as string[]) || []).length} 张`} />}
      {report&&report.can_freeze!==false&&<Alert showIcon type={missingTrainingSplits.length?'warning':'success'}
        title={missingTrainingSplits.length?splitEnabled?`可导出，暂不可训练：缺少${missingTrainingSplits.map(split=>split==='train'?'训练集':'验证集').join('、')}`:'可导出或冻结。平台训练需要训练集和验证集。':'可导出，划分满足训练要求'}
        description={missingTrainingSplits.length?<Button disabled={busy} onClick={()=>document.getElementById(splitModeId)?.focus()}>调整划分</Button>:pending?`当前划分包含 ${pending} 张待审核图片，可随冻结版本用于训练。`:'当前划分包含训练集和验证集。'}/>}
      {typeof report?.failure_reason === 'string' && report.failure_reason && <Alert type="error" showIcon title={report.failure_reason} />}
      {grouping && <Alert type="info" showIcon title={`分组结果：${grouping.group_count} 组，最大 ${grouping.largest_group} 张；实际比例：训练 ${grouping.actual_ratios.train}% · 验证 ${grouping.actual_ratios.val}% · 测试 ${grouping.actual_ratios.test}%`} />}
      {!!grouping?.empty_requested_splits?.length && <Alert type="warning" showIcon title={`整组划分后 ${grouping.empty_requested_splits.map(key => ({train:'训练集',val:'验证集',test:'测试集'})[key]).join('、')} 为空；请调整比例或补充独立图片组，同组图片不会拆开。`} />}
      {splitEnabled&&<details><summary>高级：相似图片规则与防泄漏检查</summary><Space wrap style={{ marginTop: 10 }}><Checkbox checked={checkingSimilarImages} disabled={busy || selection.group_similar} onChange={event => setSelection(previous => ({ ...previous, check_duplicates: event.target.checked }))}>启用来源分组、pHash、颜色阈值检查</Checkbox><label>pHash <InputNumber min={0} max={64} value={selection.phash_distance} disabled={busy || !checkingSimilarImages} onChange={value => setSelection(previous => ({ ...previous, phash_distance: value ?? 4 }))} /></label><label>颜色 <InputNumber min={0} max={255} value={selection.color_distance} disabled={busy || !checkingSimilarImages} onChange={value => setSelection(previous => ({ ...previous, color_distance: value ?? 20 }))} /></label><Checkbox checked={selection.require_source} disabled={busy || !checkingSimilarImages} onChange={event => setSelection(previous => ({ ...previous, require_source: event.target.checked }))}>来源缺失禁止冻结</Checkbox></Space></details>}
      <Input aria-label="冻结版本标签" placeholder="冻结版本标签（可选）" maxLength={100} value={versionLabel} onChange={event=>setVersionLabel(event.target.value)} disabled={busy}/>
    </Modal>
  </>;
}
