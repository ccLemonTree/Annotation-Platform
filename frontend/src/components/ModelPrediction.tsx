import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Select, Space, Table, Typography } from 'antd';
import AssetImage from './AssetImage';
import JsonView from './JsonView';
import type { Box } from './detectionEditor';
import { getModelServices, predictModelService, supportsModelPrompt } from '../api/modelServices';
import type { ModelService, PredictionResult } from '../api/modelServices';

export function PredictionResultView({ result, assetId, imageId }: { result: PredictionResult; assetId?: string; imageId?: string }) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  return <Space orientation="vertical" style={{ width: '100%' }} size="middle">
    <Typography.Text type="secondary">配置版本 {result.config_revision} · 模型 {result.model_service_id}</Typography.Text>
    {result.warnings?.map((warning, i) => <Alert key={i} type="warning" showIcon title={warning} />)}
    <div style={{ position: 'relative', width: '100%', maxWidth: size.width || 640, lineHeight: 0 }}>
      <AssetImage key={assetId || imageId} assetId={assetId} imageId={imageId} width="100%" preview={false} alt="预测原图"
        onLoad={event => { const image = event.target as HTMLImageElement; setSize({ width: image.naturalWidth, height: image.naturalHeight }); }} />
      {!!size.width && !!result.boxes.length && <svg aria-label="预测框预览" viewBox={`0 0 ${size.width} ${size.height}`}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
        {result.boxes.map((box, i) => <g key={i}><rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke="#1677ff" strokeWidth={2} vectorEffect="non-scaling-stroke" />
          <text x={box.x} y={Math.max(14, box.y - 4)} fill="#0958d9" fontSize={Math.max(14, size.width / 48)}>{box.label}</text></g>)}
      </svg>}
    </div>
    {result.kind === 'detection' && <>
      {!result.boxes.length && <Alert type="info" showIcon title="未检测到目标；不能自动认定为负样本，请人工核对。" />}
      <Table size="small" rowKey={(_, index) => String(index)} dataSource={result.boxes} pagination={{ pageSize: 5 }} scroll={{ x: 560 }} columns={[
        { title: '类别', dataIndex: 'label' }, { title: '置信度', dataIndex: 'score', render: value => value === undefined ? '未提供' : Number(value).toFixed(3) },
        ...['x', 'y', 'width', 'height'].map(key => ({ title: key, dataIndex: key })),
      ]} />
    </>}
    {result.caption !== undefined && <JsonView label="模型返回内容" value={result.caption}/>}
    {result.embedding_dimensions !== undefined && <>
      <div>向量维数：<Typography.Text strong>{result.embedding_dimensions}</Typography.Text></div>
      <Typography.Paragraph>前几位（最多 8 项）：{result.embedding_preview?.slice(0, 8).join(', ') || '无'}</Typography.Paragraph>
    </>}
  </Space>;
}

export interface ModelPredictionProps { assetId: string; labels: string[]; disabled: boolean; onApply: (boxes: Box[]) => void }

export default function ModelPrediction({ assetId, labels, disabled, onApply }: ModelPredictionProps) {
  const [models, setModels] = useState<ModelService[]>([]);
  const [modelId, setModelId] = useState<string>();
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<{ assetId: string; result: PredictionResult }>();
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const request = useRef(0), controller = useRef<AbortController | null>(null);
  const loadModels = async () => {
    setLoading(true); setError('');
    try { setModels((await getModelServices({ enabled: true })).filter(model => model.enabled && ['detection', 'caption'].includes(model.kind))); }
    catch { setError('模型列表读取失败，请重试'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void loadModels(); }, []);
  useEffect(() => {
    request.current++; controller.current?.abort(); setPreview(undefined); setMapping({}); setBusy(false); setError('');
    return () => { request.current++; controller.current?.abort(); };
  }, [assetId, disabled, modelId]);
  const model = models.find(item => item.id === modelId);
  const result = preview?.assetId === assetId ? preview.result : undefined;
  const unknown = [...new Set(result?.boxes.map(box => box.label).filter(label => !labels.includes(label)) || [])];
  const canApply = !disabled && result?.kind === 'detection' && result.boxes.length > 0 && unknown.every(label => labels.includes(mapping[label]));
  const run = async () => {
    if (disabled || !assetId || !model || busy) return;
    const current = ++request.current;
    controller.current?.abort(); controller.current = new AbortController();
    setBusy(true); setError(''); setPreview(undefined); setMapping({});
    try {
      const prediction = await predictModelService(model.id, { asset_id: assetId, ...(supportsModelPrompt(model) && prompt.trim() ? { prompt: prompt.trim() } : {}) }, controller.current.signal);
      if (current === request.current) setPreview({ assetId, result: prediction });
    } catch { if (current === request.current) setError('预测失败，当前草稿已保留，请检查模型配置后重试'); }
    finally { if (current === request.current) setBusy(false); }
  };
  const apply = () => {
    if (!canApply || !result) return;
    onApply(result.boxes.map(box => ({ x: box.x, y: box.y, width: box.width, height: box.height, label: labels.includes(box.label) ? box.label : mapping[box.label] })));
    setPreview(undefined);
  };
  return <Space orientation="vertical" size="small" style={{ width: '100%' }}>
    <Select aria-label="预测模型" placeholder={loading ? '正在读取模型…' : '选择检测 / 描述模型'} loading={loading} disabled={disabled || loading || busy}
      value={modelId} onChange={setModelId} style={{ width: '100%' }} options={models.map(item => ({ value: item.id, label: item.name }))} />
    <Input.TextArea aria-label="预测提示词" placeholder={model?.kind === 'detection' ? '检测类别，用分号分隔；留空使用模型配置类别' : '本次描述提示词（可选）；留空使用模型配置中的描述核心提示词'} value={prompt} onChange={event => setPrompt(event.target.value)} disabled={disabled || busy || !supportsModelPrompt(model)} maxLength={2000} autoSize={{ minRows: 1, maxRows: 3 }} />
    <Button aria-label="预测预览" onClick={run} loading={busy} disabled={disabled || !assetId || !model || busy} block>预测预览</Button>
    {!loading && !models.length && !error && <Typography.Text type="secondary">暂无启用的检测或描述模型，请联系管理员配置。</Typography.Text>}
    {error && <Alert type="error" showIcon title={error} action={<Button size="small" onClick={loadModels} disabled={disabled || busy}>重试</Button>} />}
    <Modal title="预测结果预览" open={!!result && !disabled} width={760} onCancel={() => setPreview(undefined)}
      styles={{ body: { maxHeight: '65vh', overflowY: 'auto' } }} footer={<Space>
        <Button aria-label="关闭" onClick={() => setPreview(undefined)}>关闭</Button>
        <Button type="primary" onClick={apply} disabled={!canApply}>应用到草稿</Button>
      </Space>}>
      {result && <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <PredictionResultView result={result} assetId={assetId} />
        {unknown.length > 0 && <Alert type="warning" showIcon title="存在项目外类别，请明确映射后再应用。" />}
        {unknown.map(label => <div key={label}><Typography.Text>{label} → </Typography.Text><Select aria-label={`映射 ${label}`} placeholder="请选择项目类别"
          style={{ minWidth: 180 }} value={mapping[label]} onChange={value => setMapping(previous => ({ ...previous, [label]: value }))}
          options={labels.map(value => ({ label: value, value }))} /></div>)}
        {result.kind === 'detection' && result.boxes.length > 0 && <Alert type="warning" showIcon title="应用将替换当前草稿中的全部框，可撤销；仅修改草稿，请核对后保存。" />}
        {result.kind !== 'detection' && <Typography.Text type="secondary">图像描述仅供参考，不会生成或替换标注框。</Typography.Text>}
      </Space>}
    </Modal>
  </Space>;
}
