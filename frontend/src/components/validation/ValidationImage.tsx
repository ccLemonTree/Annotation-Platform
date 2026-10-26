import { useEffect, useState, type ReactNode } from 'react';
import { Alert, Button, Modal, Space, Spin, Typography, theme } from 'antd';
import { getValidationImage, validationError, validationErrorLabels, validationStatusLabels } from '../../api/validation';
import type { ValidationItem } from '../../api/validation';

export default function ValidationImage({ runId, item, confidence, onClose, navigation }: { runId: string; item: ValidationItem; confidence:number; onClose: () => void; navigation:ReactNode }) {
  const { token } = theme.useToken();
  const [url, setUrl] = useState(''), [loaded, setLoaded] = useState(false), [error, setError] = useState(''), [revision, setRevision] = useState(0);
  const [size, setSize] = useState({ width: item.width, height: item.height });
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    setUrl(''); setLoaded(false); setError('');
    getValidationImage(runId, item.id, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(e => { if (!controller.signal.aborted) setError(validationError(e, '原图读取失败')); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [runId, item.id, revision]);
  const validBoxes = Array.isArray(item.truth) && Array.isArray(item.predictions);
  const merged=item.evaluation?.ignored_duplicates||[];
  return <Modal open title="真值与预测" width={1000} onCancel={onClose} footer={<Button onClick={onClose}>关闭预览</Button>} styles={{ body: { maxHeight: '70dvh', overflowY: 'auto' } }}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      {navigation}
      <Typography.Text>{item.original_name} · {validationStatusLabels[item.status] || item.status}</Typography.Text>
      {item.status==='completed'&&<Typography.Text>当前判定：TP {item.evaluation?.tp} · FP {item.evaluation?.fp} · FN {item.evaluation?.fn}；已合并重复框 {merged.length} 个。</Typography.Text>}
      {item.status==='completed'&&<Typography.Text>漏检标签：{validationErrorLabels(item,'false_negative')}；误报标签：{validationErrorLabels(item,'false_positive')}。</Typography.Text>}
      {item.error && <Alert type="error" title={item.error} />}
      {!validBoxes && <Alert type="error" title="框数据格式不符，无法显示叠框，请刷新或联系管理员。" />}
      <Space wrap><Typography.Text style={{ color: token.colorPrimary }}>蓝色实线：标注真值</Typography.Text><Typography.Text style={{ color: token.colorError }}>红色实线：漏检真值</Typography.Text><Typography.Text style={{ color: token.colorWarning }}>虚线：预测；灰色为低于工作点 {confidence} 的预测</Typography.Text></Space>
      <Typography.Paragraph type="secondary">显示原始预测框（含低于工作点的框，已合并重复框除外）；指标按报告当前判定规则过滤。</Typography.Paragraph>
      {!!merged.length&&<Typography.Paragraph type="secondary">同一目标的重复预测框已合并并隐藏；原始预测仍完整保留在 JSON 报告中。</Typography.Paragraph>}
      {error ? <Alert type="error" title={error} action={<Button onClick={() => setRevision(n => n + 1)}>重试原图</Button>} /> : <>
        {!loaded && <Spin description="读取原图…"><div style={{ minHeight: 48 }} /></Spin>}
        {url && <div style={{ position: 'relative', width: '100%', lineHeight: 0 }}>
          <img src={url} alt={item.original_name} style={{ display: 'block', width: '100%', height: 'auto' }}
            onLoad={event => { setLoaded(true); setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight }); }}
            onError={() => setError('原图解码失败，请重试')} />
          {loaded && validBoxes && size.width > 0 && size.height > 0 && <svg role="img" aria-label="真值与预测叠框" viewBox={`0 0 ${size.width} ${size.height}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
            {[{ boxes: item.truth, prediction: false, color: token.colorPrimary }, { boxes: item.predictions, prediction: true, color: token.colorWarning }].map(group => group.boxes.map((box, i) => {
              if(group.prediction&&merged.includes(i))return null;
              const missing=!group.prediction&&item.evaluation?.unmatched_truth?.includes(i);
              const low=group.prediction&&'score' in box&&typeof box.score==='number'&&box.score<confidence;
              const color=missing?token.colorError:low?token.colorTextDisabled:group.color;
              return <g key={`${group.prediction}:${i}`}>
              <rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke={color} strokeWidth={2} strokeDasharray={group.prediction ? '6 3' : undefined} vectorEffect="non-scaling-stroke" />
              <text x={box.x} y={group.prediction ? Math.min(size.height - 4, box.y + box.height - 4) : Math.max(16, box.y - 4)} fill={color} stroke={token.colorBgContainer} strokeWidth={2} paintOrder="stroke" fontSize={Math.max(16, size.width / 45)}>
                {box.label}{missing?'（漏检）':''}{group.prediction && 'score' in box && typeof box.score === 'number' ? ` ${box.score.toFixed(3)}` : ''}{low?'（低分）':''}
              </text>
            </g>;}))}
          </svg>}
        </div>}
      </>}
      {validBoxes && !item.truth.length && <Typography.Text>真值：确认负样本（无目标框）</Typography.Text>}
      {validBoxes && !item.predictions.length && <Typography.Text>{item.status === 'completed' ? '模型预测：无目标框' : '尚无成功推理结果'}</Typography.Text>}
    </Space>
  </Modal>;
}
