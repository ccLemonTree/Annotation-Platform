import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Select, Space, Table, Typography } from 'antd';
import { useAssetImageUrl } from './AssetImage';
import DetectionBoxOverlay from './DetectionBoxOverlay';
import JsonView from './JsonView';
import { labelColor } from './detectionEditor';
import type { Box } from './detectionEditor';
import PromptEditor from './PromptEditor';
import { getModelServices,isModelOffline, predictModelService, supportsModelPrompt } from '../api/modelServices';
import type { ModelService, PredictionResult } from '../api/modelServices';
import { addProjectLabels, type Project } from '../api/projects';

export function PredictionResultView({ result, assetId, imageId, labels }: { result: PredictionResult; assetId?: string; imageId?: string; labels?: string[] }) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const { source: imageSrc, failed: imageFailed } = useAssetImageUrl({ assetId, imageId, fullSize: true });
  useEffect(() => { setSize({ width: 0, height: 0 }); }, [assetId, imageId]);
  return <Space orientation="vertical" style={{ width: '100%' }} size="middle">
    <Typography.Text type="secondary">配置版本 {result.config_revision} · 模型 {result.model_service_id}</Typography.Text>
    {result.warnings?.map((warning, i) => <Alert key={i} type="warning" showIcon title={warning} />)}
    {(assetId || imageId) && <div style={{ position: 'relative', width: '100%', maxWidth: size.width || 640, lineHeight: 0 }}>
      {imageFailed ? <span>图片加载失败</span> : imageSrc && (
        <DetectionBoxOverlay
          key={assetId || imageId}
          src={imageSrc}
          width={size.width}
          height={size.height}
          boxes={result.boxes}
          labels={labels}
          showScore={false}
          alt="预测原图"
          svgLabel="预测框预览"
          onImageLoad={setSize}
        />
      )}
    </div>}
    {result.kind === 'detection' && <>
      {!result.boxes.length && <Alert type="info" showIcon title="未检测到目标；不能自动认定为负样本，请人工核对。" />}
      <Table size="small" rowKey={(_, index) => String(index)} dataSource={result.boxes} pagination={{ pageSize: 5 }} scroll={{ x: 560 }} columns={[
        { title: '类别', dataIndex: 'label', render: (value: string) => <><span aria-hidden="true" style={{display:'inline-block',width:12,height:12,background:labelColor(value, labels),border:'1px solid #333',marginRight:8}}/>{value}</> }, { title: '置信度', dataIndex: 'score', render: value => value === undefined ? '未提供' : Number(value).toFixed(3) },
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

export interface ModelPredictionProps { assetId: string; projectId: string; labels: string[]; disabled: boolean; onApply: (boxes: Box[], project?: Project) => void }

export default function ModelPrediction({ assetId, projectId, labels, disabled, onApply }: ModelPredictionProps) {
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
    try { setModels((await getModelServices({ enabled: true })).filter(model => model.enabled && model.protocol !== 'jev_decide' && ['detection', 'caption'].includes(model.kind))); }
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
  const canApply = !disabled && !busy && result?.kind === 'detection' && result.boxes.length > 0;
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
  const apply = async () => {
    if (!canApply || !result) return;
    const current = ++request.current;
    const boxes = result.boxes.map(({x,y,width,height,label}) => ({x,y,width,height,label:labels.includes(label)?label:mapping[label]||label.trim()}));
    const newLabels = [...new Set(boxes.map(box => box.label).filter(label => !labels.includes(label)))];
    setBusy(true); setError('');
    try {
      const project = newLabels.length ? await addProjectLabels(projectId, newLabels) : undefined;
      if (current === request.current) { onApply(boxes,project); setPreview(undefined); }
    } catch { if (current === request.current) setError('添加模型类别失败，当前草稿已保留，请重试'); }
    finally { if (current === request.current) setBusy(false); }
  };
  return <Space orientation="vertical" size="small" style={{ width: '100%' }}>
    <Select aria-label="预测模型" placeholder={loading ? '正在读取模型…' : '选择检测 / 描述模型'} loading={loading} disabled={disabled || loading || busy}
      value={modelId} onChange={setModelId} style={{ width: '100%' }} options={models.map(item => ({ value: item.id, disabled:isModelOffline(item), label:item.name+(isModelOffline(item)?' · 离线':'') }))} />
    <PromptEditor key={modelId??'none'} title="预测提示词" buttonLabel="编辑提示词" ariaLabel="预测提示词" help={model?.kind === 'detection' ? '检测类别，用分号分隔；留空使用模型配置类别' : '本次描述提示词（可选）；留空使用模型配置中的描述核心提示词'}
      defaultValue={model?.kind==='caption'?model.chat_prompts?.caption:undefined} referencePrompt={model?.kind==='detection'?model.labels?.join('；'):undefined}
      referenceLabel={model?.kind==='detection'?'模型默认检测类别':undefined} value={prompt} onChange={setPrompt} disabled={disabled || busy || !supportsModelPrompt(model)} maxLength={2000} />
    <Button aria-label="预测预览" onClick={run} loading={busy} disabled={disabled || !assetId || !model || busy} block>预测预览</Button>
    {!loading && !models.length && !error && <Typography.Text type="secondary">暂无启用的检测或描述模型，请联系管理员配置。</Typography.Text>}
    {error && !result && <Alert type="error" showIcon title={error} action={<Button size="small" onClick={loadModels} disabled={disabled || busy}>重试</Button>} />}
    <Modal title="预测结果预览" open={!!result && !disabled} width={760} onCancel={() => { if(!busy){setPreview(undefined);setError('');} }}
      styles={{ body: { maxHeight: '65vh', overflowY: 'auto' } }} footer={<Space>
        <Button aria-label="关闭" disabled={busy} onClick={() => {setPreview(undefined);setError('');}}>关闭</Button>
        <Button type="primary" onClick={apply} loading={busy} disabled={!canApply}>应用到草稿</Button>
      </Space>}>
      {result && <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <PredictionResultView result={result} assetId={assetId} labels={labels} />
        {error && <Alert type="error" showIcon title={error} />}
        {unknown.length > 0 && <Alert type="info" showIcon title="应用时会把新类别加入项目。也可选择映射到已有类别。" />}
        {unknown.map(label => <div key={label}><Typography.Text>{label} → </Typography.Text><Select aria-label={`映射 ${label}`} placeholder="直接使用模型类别"
          style={{ minWidth: 180 }} allowClear disabled={busy} value={mapping[label]} onChange={value => setMapping(previous => ({ ...previous, [label]: value }))}
          options={labels.map(value => ({ label: value, value }))} /></div>)}
        {result.kind === 'detection' && result.boxes.length > 0 && <Alert type="warning" showIcon title="应用将替换当前草稿中的全部框，可撤销；仅修改草稿，请核对后保存。" />}
        {result.kind !== 'detection' && <Typography.Text type="secondary">图像描述仅供参考，不会生成或替换标注框。</Typography.Text>}
      </Space>}
    </Modal>
  </Space>;
}
