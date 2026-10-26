import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Input, Modal, Space, Spin, Typography } from 'antd';
import { getAnnotation } from '../../api/annotations';
import { getAsset } from '../../api/assets';
import { resolveAutomationItem } from '../../api/automation';
import type { AutomationItem } from '../../api/automation';
import type { Annotation } from '../../api/annotations';
import type { Box } from '../detectionEditor';
import AssetImage from '../AssetImage';
import AutomationEvidence from './AutomationEvidence';

export default function AutomationReviewPreview({ item, projectId, onClose, onResolved }: {
  item: AutomationItem; projectId?: string; onClose: () => void; onResolved: () => void;
}) {
  const [annotation, setAnnotation] = useState<Annotation>();
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [assetProjectId, setAssetProjectId] = useState<string>();
  const [error, setError] = useState(''), [comment, setComment] = useState('');
  const [conflicted, setConflicted] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const saving = useRef(false), active = useRef(true);
  useEffect(() => {
    active.current = true;
    let current = true;
    Promise.allSettled([getAsset(item.asset_id), item.annotation_id ? getAnnotation(item.annotation_id) : Promise.resolve(undefined)])
      .then(([assetResult, annotationResult]) => {
        if (!current) return;
        const asset = assetResult.status === 'fulfilled' ? assetResult.value : undefined;
        const record = annotationResult.status === 'fulfilled' ? annotationResult.value : undefined;
        setAssetProjectId(asset?.project_id); setAnnotation(record);
        if (assetResult.status === 'rejected' || annotationResult.status === 'rejected') {
          setError('图片或标注读取失败，已禁用批准；可驳回移出旧候选。');
        }
        if (record && (record.asset_id !== item.asset_id || !record.is_latest || record.version !== item.annotation_version)) {
          setConflicted(true); setError('标注版本已变化，请刷新队列后重新核对。');
        }
        if (projectId && asset && asset.project_id !== projectId) { setConflicted(true); setError('图片项目已变化，请刷新队列后重新核对。'); }
      })
        .finally(() => { if (current) setLoading(false); });
    return () => { current = false; active.current = false; };
  }, [item.annotation_id, item.asset_id, item.annotation_version, projectId]);
  const rawBoxes = annotation?.annotation_data?.boxes ?? item.result?.boxes;
  const boxes: Box[] = Array.isArray(rawBoxes) ? rawBoxes : [];
  const canApprove = !loading && !conflicted && !error && !!size.width && !!item.annotation_id && !!annotation && !!assetProjectId;
  const resolve = async (approved: boolean) => {
    if (saving.current || loading || (approved && !canApprove)) return;
    saving.current = true; setBusy(true);
    try {
      await resolveAutomationItem(item.id, { approved, comment });
      if (active.current) onResolved();
    } catch {
      if (active.current) { setConflicted(true); setError('审核未保存，请刷新队列后重新核对标注版本。'); }
    } finally {
      saving.current = false; if (active.current) setBusy(false);
    }
  };
  const search = new URLSearchParams({ from: 'review', ...((assetProjectId || projectId) ? { project_id: assetProjectId || projectId } : {}) });
  return <Modal open title="管理员审核预览" width={1000} styles={{ body: { maxHeight: '65dvh', overflowY: 'auto' } }} onCancel={() => { if (!saving.current) onClose(); }} closable={!busy} mask={{ closable: !busy }}
    footer={<Space wrap>
      <Button disabled={busy} onClick={onClose}>关闭预览</Button>
      <Button aria-label="驳回" danger disabled={loading || busy} loading={busy} onClick={() => { void resolve(false); }}>驳回</Button>
      <Button aria-label="批准" type="primary" disabled={!canApprove || busy} loading={busy} onClick={() => { void resolve(true); }}>批准</Button>
    </Space>}>
    <Space orientation="vertical" style={{ width: '100%' }} size="middle">
      {error && <Alert type="error" showIcon title={error} />}
      {!item.annotation_id && <Alert type="warning" showIcon title="候选框尚未保存；可以驳回移出队列，或打开标注工作台修正类别并保存。" />}
      <Alert type="info" showIcon title="驳回仅移出旧候选，不修改新的人工标注" description="无标注、标注过期、原图删除或类别变动的旧候选均可驳回。批准仍须核对原图与当前标注版本。" />
      {loading && <Spin description="读取任务所引用的标注版本…"><div style={{ minHeight: 40 }} /></Spin>}
      <Typography.Text>标注来源：{annotation?.annotation_source === 'manual' ? '人工（AI审核不修改人工框）' : annotation?.annotation_source || '尚无标注'}；版本：{annotation?.version ?? '—'}</Typography.Text>
      <div style={{ position: 'relative', width: '100%', maxWidth: size.width || 640, lineHeight: 0 }}>
        <AssetImage assetId={item.asset_id} width="100%" alt="审核原图" preview={false}
          onLoad={event => { const image = event.target as HTMLImageElement; setSize({ width: image.naturalWidth, height: image.naturalHeight }); }} />
        {!!size.width && !!boxes.length && <svg aria-label="审核框预览" role="img" viewBox={`0 0 ${size.width} ${size.height}`}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
          {boxes.map((box, index) => <g key={index}>
            <rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke="#1677ff" strokeWidth={2} vectorEffect="non-scaling-stroke" />
            <text x={box.x} y={Math.max(16, box.y - 4)} fill="#0958d9" fontSize={Math.max(16, size.width / 48)}>{index + 1} · {box.label}</text>
          </g>)}
        </svg>}
      </div>
      {!loading && !boxes.length && <Alert type="warning" showIcon title="零框不能自动认定为负样本，请管理员核对原图后决定。" />}
      {assetProjectId && <Link to={`/annotate/${encodeURIComponent(item.asset_id)}?${search}`}>打开标注工作台</Link>}
      <AutomationEvidence item={item} />
      <label htmlFor="automation-review-comment">审核评论</label>
      <Input.TextArea id="automation-review-comment" aria-label="审核评论" maxLength={2000} showCount value={comment} disabled={busy} onChange={event => setComment(event.target.value)} autoSize={{ minRows: 2, maxRows: 6 }} />
    </Space>
  </Modal>;
}
