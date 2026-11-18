import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Input, Modal, Space, Spin, Typography } from 'antd';
import { getAnnotation } from '../../api/annotations';
import { getAsset } from '../../api/assets';
import { getProject } from '../../api/projects';
import { resolveAutomationItem } from '../../api/automation';
import type { AutomationItem } from '../../api/automation';
import type { Annotation } from '../../api/annotations';
import type { Box } from '../detectionEditor';
import { useAssetImageUrl } from '../AssetImage';
import DetectionBoxOverlay from '../DetectionBoxOverlay';
import AutomationEvidence from './AutomationEvidence';

export default function AutomationReviewPreview({ item, projectId, from='review', onClose, onResolved }: {
  item: AutomationItem; projectId?: string; from?:'review'|'automation-review'; onClose: () => void; onResolved: () => void;
}) {
  const [annotation, setAnnotation] = useState<Annotation>();
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [assetProjectId, setAssetProjectId] = useState<string>();
  const [labels, setLabels] = useState<string[]>([]);
  const [labelsLoading, setLabelsLoading] = useState(false), [labelsError, setLabelsError] = useState(''), [labelsRevision, setLabelsRevision] = useState(0);
  const [error, setError] = useState(''), [comment, setComment] = useState('');
  const [conflicted, setConflicted] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const { source: imageSrc, failed: imageFailed } = useAssetImageUrl({ assetId: item.asset_id });
  const saving = useRef(false), active = useRef(true);
  useEffect(() => { setSize({ width: 0, height: 0 }); }, [item.asset_id]);
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
  useEffect(() => {
    let current = true; setLabels([]); setLabelsError('');
    if (!assetProjectId) { setLabelsLoading(false); return () => { current = false; }; }
    setLabelsLoading(true);
    getProject(assetProjectId).then(project => {
      if (current) setLabels(Array.isArray(project.label_schema?.labels) ? project.label_schema.labels.filter((label): label is string => typeof label === 'string' && !!label.trim()) : []);
    }).catch(() => { if (current) setLabelsError('项目类别读取失败，框颜色暂不可用。'); })
      .finally(() => { if (current) setLabelsLoading(false); });
    return () => { current = false; };
  }, [assetProjectId, labelsRevision]);
  const rawBoxes = annotation?.annotation_data?.boxes ?? item.result?.boxes;
  const boxes: Box[] = Array.isArray(rawBoxes) ? rawBoxes : [];
  const canApprove = !loading && !labelsLoading && !labelsError && !conflicted && !error && !!size.width && !!item.annotation_id && !!annotation && !!assetProjectId;
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
  const search = new URLSearchParams({ from, ...((assetProjectId || projectId) ? { project_id: assetProjectId || projectId } : {}) });
  return <Modal open title="管理员审核预览" width={1000} styles={{ body: { maxHeight: '65dvh', overflowY: 'auto' } }} onCancel={() => { if (!saving.current) onClose(); }} closable={!busy} mask={{ closable: !busy }}
    footer={<Space wrap>
      <Button disabled={busy} onClick={onClose}>关闭预览</Button>
      <Button aria-label="驳回" danger disabled={loading || busy} loading={busy} onClick={() => { void resolve(false); }}>驳回</Button>
      <Button aria-label="批准" type="primary" disabled={!canApprove || busy} loading={busy} onClick={() => { void resolve(true); }}>批准</Button>
    </Space>}>
    <Space orientation="vertical" style={{ width: '100%' }} size="middle">
      {(item.project_name||item.asset_name)&&<Typography.Text strong>{item.project_name||'当前项目'} · {item.asset_name||'原图已移除'}</Typography.Text>}
      {error && <Alert type="error" showIcon title={error} />}
      {labelsLoading && <Alert type="info" showIcon title="正在读取项目类别…" />}
      {labelsError && <Alert type="error" showIcon title={labelsError} action={<Button size="small" disabled={busy} onClick={() => setLabelsRevision(value => value + 1)}>重试读取项目类别</Button>} />}
      {!item.annotation_id && <Alert type="warning" showIcon title="候选框尚未保存；可以驳回移出队列，或打开标注工作台修正类别并保存。" />}
      <Alert type="info" showIcon title="驳回仅移出旧候选，不修改新的人工标注" description="无标注、标注过期、原图删除或类别变动的旧候选均可驳回。批准仍须核对原图与当前标注版本。" />
      {loading && <Spin description="读取任务所引用的标注版本…"><div style={{ minHeight: 40 }} /></Spin>}
      <Typography.Text>标注来源：{annotation?.annotation_source === 'manual' ? '人工（AI审核不修改人工框）' : annotation?.annotation_source === 'reuse' ? '项目复用' : annotation?.annotation_source || '尚无标注'}；版本：{annotation?.version ?? '—'}</Typography.Text>
      <div style={{ position: 'relative', width: '100%', maxWidth: size.width || 640, lineHeight: 0 }}>
        {imageFailed ? <span>图片加载失败</span> : imageSrc && (
          <DetectionBoxOverlay
            src={imageSrc}
            width={size.width}
            height={size.height}
            boxes={boxes}
            labels={labels}
            neutral={labelsLoading || !!labelsError}
            showIndex
            showScore={false}
            alt="审核原图"
            svgLabel="审核框预览"
            onImageLoad={setSize}
          />
        )}
      </div>
      {!loading && !boxes.length && <Alert type="warning" showIcon title="零框不能自动认定为负样本，请管理员核对原图后决定。" />}
      {assetProjectId && <Link to={`/annotate/${encodeURIComponent(item.asset_id)}?${search}`}>打开标注工作台</Link>}
      <AutomationEvidence item={item} />
      <label htmlFor="automation-review-comment">审核评论</label>
      <Input.TextArea id="automation-review-comment" aria-label="审核评论" maxLength={2000} showCount value={comment} disabled={busy} onChange={event => setComment(event.target.value)} autoSize={{ minRows: 2, maxRows: 6 }} />
    </Space>
  </Modal>;
}
