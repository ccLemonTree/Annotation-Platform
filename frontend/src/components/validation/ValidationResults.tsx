import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Descriptions, Empty, InputNumber, Select, Space, Table, Typography } from 'antd';
import type { ValidationErrorType, ValidationItem, ValidationRun, ValidationView } from '../../api/validation';
import { activeValidationRun, getValidationItems, getValidationReport, getValidationRun, validationError, validationErrorLabels, validationStatusLabels } from '../../api/validation';
import useAutomationFeed from '../automation/useAutomationFeed';
import ValidationImage from './ValidationImage';
import useImageNavigation from '../useImageNavigation';

const noPolling = () => false;
const number = (value: number | null | undefined) => value == null ? '—' : value;
const ratio = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`;

function ValidationItems({ run, view }: { run: ValidationRun; view: ValidationView }) {
  const [page, setPage] = useState(1), [pageSize, setPageSize] = useState(20), [filter, setFilter] = useState<ValidationErrorType>();
  const [label,setLabel]=useState<string>();
  const [preview, setPreview] = useState<ValidationItem>(), [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => getValidationItems(run.id, { page, page_size: pageSize, error_type: filter, label, ...view }, signal), [run.id, page, pageSize, filter, label, view]);
  const { data, loading, error } = useAutomationFeed(`${run.id}:${page}:${pageSize}:${filter || ''}:${label||''}`, load, noPolling, `${run.updated_at}:${run.processed}:${run.status}:${revision}`);
  const navigation=useImageNavigation({scope:`${run.id}:${filter||''}:${label||''}:${pageSize}`,items:data?.items,page,pageSize,total:data?.total||0,onPage:setPage,enabled:!!preview,modal:true});
  useEffect(()=>{if(preview&&navigation.item&&preview.id!==navigation.item.id)setPreview(navigation.item);},[preview,navigation.item]);
  return <Card title="逐图结果" styles={{ body: { minWidth: 0 } }}>
    <Space wrap style={{ marginBottom: 16 }}>
      <label htmlFor="validation-error-type">错误类型</label>
      <Select id="validation-error-type" aria-label="错误类型" value={filter} allowClear placeholder="全部样本" style={{ width: 180 }} onChange={value => { setFilter(value); setPage(1); setPreview(undefined); }}
        options={[{ value: 'false_positive', label: '误检' }, { value: 'false_negative', label: '漏检' }, { value: 'class_confusion', label: '类别混淆' }, { value: 'failed', label: '推理失败' }, { value: 'correct', label: '正确' }]} />
      <label htmlFor="validation-error-label">标签</label>
      <Select id="validation-error-label" aria-label="错误标签" value={label} allowClear showSearch optionFilterProp="label" placeholder="全部标签" style={{width:180}}
        options={(run.labels||run.metrics?.per_class?.map(row=>row.label)||[]).map(value=>({value,label:value}))}
        onChange={value=>{setLabel(value);setPage(1);setPreview(undefined);}}/>
      <Button aria-label="刷新逐图结果" onClick={() => setRevision(n => n + 1)} loading={loading}>刷新逐图结果</Button>
    </Space>
    {error && <Alert type="error" title="逐图结果读取失败，请重试；当前数据可能已过期。" />}
    <Table rowKey="id" size="small" loading={loading} dataSource={data?.items || []} scroll={{ x: 850 }} locale={{ emptyText: '暂无符合条件的样本' }} columns={[
      { title: '原图', dataIndex: 'original_name', width: 220, ellipsis: true },
      { title: '状态', dataIndex: 'status', width: 120, render: status => validationStatusLabels[status] || status },
      { title: 'TP', render: (_, item) => number(item.evaluation?.tp) },
      { title: 'FP', render: (_, item) => number(item.evaluation?.fp) },
      { title: 'FN', render: (_, item) => number(item.evaluation?.fn) },
      { title: '漏检标签', render: (_, item) => validationErrorLabels(item,'false_negative') },
      { title: '误报标签', render: (_, item) => validationErrorLabels(item,'false_positive') },
      { title: '合并重复框', render: (_, item) => item.evaluation?.ignored_duplicates?.length || 0 },
      { title: '错误', dataIndex: 'error', width: 230, render: value => value || '—' },
      { title: '操作', width: 120, render: (_, item) => <Button onClick={() => {navigation.select(item.id);setPreview(item);}}>查看叠框</Button> },
    ]} pagination={{ current: page, pageSize, total: data?.total || 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100], onChange: (next, size) => { navigation.select('');setPage(size === pageSize ? next : 1); setPageSize(size); } }} />
    {preview && <ValidationImage key={`${run.id}:${(navigation.item||preview).id}`} runId={run.id} item={navigation.item||preview} confidence={run.config.confidence} onClose={() => setPreview(undefined)}
      navigation={<Space wrap><Button aria-label="上一张图片" disabled={!navigation.canPrevious} onClick={navigation.previous}>← 上一张</Button><span>{data?`${navigation.position} / ${data.total}`:error?'读取失败，请关闭后刷新重试':'正在读取…'} · 左右方向键切图</span><Button aria-label="下一张图片" disabled={!navigation.canNext} onClick={navigation.next}>下一张 →</Button></Space>} />}
  </Card>;
}

export default function ValidationResults({ runId, revision, onClose }: { runId: string; revision: number; onClose: () => void }) {
  const [reload, setReload] = useState(0), [downloading, setDownloading] = useState(false), [reportError, setReportError] = useState('');
  const [mode,setMode]=useState<ValidationView['mode']>('target'),[overlapOverride,setTargetOverlap]=useState<number>();
  const view=useMemo(()=>({mode,target_overlap:overlapOverride}),[mode,overlapOverride]);
  const viewKey=`${runId}:${mode}:${overlapOverride??'task'}`;
  const load = useCallback((signal: AbortSignal) => getValidationRun(runId, signal,view), [runId,view]);
  const { data: run, loading, error } = useAutomationFeed(viewKey, load, activeValidationRun, `${revision}:${reload}`);
  const targetOverlap=overlapOverride??run?.config.target_overlap??.2;
  const metrics = mode==='target'?run?.target_metrics:run?.metrics, coverage = run?.metrics?.coverage, matrix = metrics?.confusion_matrix;
  const download = async () => {
    if (downloading) return;
    setDownloading(true); setReportError('');
    try {
      const blob = await getValidationReport(runId,view);
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      try { link.href = url; link.download = `validation-${runId}.json`; link.click(); }
      finally { URL.revokeObjectURL(url); }
    } catch (e) { setReportError(validationError(e, '报告下载失败')); }
    finally { setDownloading(false); }
  };
  const cells = [
    { key: 'tp', label: 'TP', value: number(metrics?.tp) }, { key: 'fp', label: 'FP', value: number(metrics?.fp) }, { key: 'fn', label: 'FN', value: number(metrics?.fn) },
    { key: 'precision', label: 'Precision', value: ratio(metrics?.precision) }, { key: 'recall', label: 'Recall', value: ratio(metrics?.recall) }, { key: 'f1', label: 'F1', value: ratio(metrics?.f1) },
    { key: 'map50', label: '标准 mAP50', value: ratio(run?.metrics?.map50) }, { key: 'map50_95', label: '标准 mAP50:95', value: ratio(run?.metrics?.map50_95) },
  ];
  return <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
    <Card title="验证报告" styles={{ body: { minWidth: 0 } }} extra={<Button onClick={onClose}>关闭结果</Button>}>
      <Space wrap style={{marginBottom:16}}>
        <label htmlFor="validation-match-mode">判定方式</label>
        <Select id="validation-match-mode" aria-label="判定方式" value={mode} onChange={setMode} style={{width:250}}
          options={[{value:'target',label:'目标检出（合并重复框）'},{value:'standard',label:'标准框匹配（原任务参数）'}]}/>
        {mode==='target'&&<><label htmlFor="validation-target-overlap">交集阈值（%）</label><InputNumber id="validation-target-overlap" aria-label="交集阈值（%）" min={1} max={100} step={5} precision={0} value={Math.round(targetOverlap*100)} onChange={value=>{if(value!==null&&value>=1&&value<=100)setTargetOverlap(value/100);}}/></>}
      </Space>
      {mode==='target'&&<Typography.Paragraph type="secondary">同类别且交集面积 ÷ 较小框面积 ≥ {Math.round(targetOverlap*100)}% 算检出，完全包含也算；同一真值目标的重复框合并计一次。P/R/F1、错误筛选和混淆矩阵按此规则；标准 AP 仍按原规则计算。只重新计算已有预测，不发起推理。</Typography.Paragraph>}
      <Space wrap style={{ marginBottom: 16 }}><Button aria-label="刷新报告" onClick={() => setReload(n => n + 1)} loading={loading}>刷新报告</Button><Button aria-label="下载 JSON 报告" onClick={() => { void download(); }} loading={downloading}>下载 JSON 报告</Button></Space>
      {error && <Alert type="error" title="任务详情读取失败，请重试；当前报告可能已过期。" />}
      {reportError && <Alert type="error" title={reportError} />}
      {run?.error && <Alert type="error" title={run.error} />}
      {run && <>
        <Typography.Paragraph>{run.name || run.id} · {run.model.name} · r{run.model.revision} · {validationStatusLabels[run.status] || run.status}</Typography.Paragraph>
        {run.model.source === 'checkpoint' && <Typography.Paragraph type="secondary" style={{ overflowWrap: 'anywhere' }}>训练权重：{run.model.weight_path}<br />SHA256：{run.model.weight_sha256}<br />模型类型：{run.model.kind} · 设备：{run.model.device}<br />执行镜像：{run.model.image_digest}</Typography.Paragraph>}
        <Typography.Paragraph type="secondary">原任务参数：推理置信度 {run.config.inference_confidence}（标准 AP50:95 使用）；工作点 {run.config.confidence}（P/R/F1）；标准框匹配 IoU {run.config.match_iou}；NMS IoU {run.config.nms_iou}。</Typography.Paragraph>
        <Typography.Paragraph type="secondary">每图每类最多 100 个预测参与指标；无面积 / crowd 专用规则，不等同完整 COCO 评测。仅统计成功样本，类别无真值时 AP 为“—”。</Typography.Paragraph>
        {run.model.source==='checkpoint'&&activeValidationRun(run)&&run.processed===0&&<Alert type="info" showIcon title="正在准备数据、上传或批量推理，尚未入库结果" description="准备、上传和远端批量推理阶段不增加已处理数；结果回传并完成校验评测后才开始增长。失败、取消的样本也计入已处理总数；准备阶段 GPU 可能暂时空闲。"/>}
        {!coverage?.complete && <Alert type="warning" showIcon title="部分结果：覆盖不完整，不能代表整个验证集" description={coverage ? `成功 ${coverage.succeeded} / ${coverage.total}；失败 ${coverage.failed}；待处理 ${coverage.pending}；取消 ${coverage.canceled}。` : `已处理 ${run.processed} / ${run.total}；失败 ${run.failed_count}。覆盖统计尚不可用。`} />}
        {coverage?.complete && <Typography.Paragraph>覆盖：成功 {coverage.succeeded} / {coverage.total}；失败 {coverage.failed}；待处理 {coverage.pending}；取消 {coverage.canceled}。</Typography.Paragraph>}
      </>}
      {metrics?.images == null && <Empty description={loading ? '报告读取中' : '尚无可用指标'} image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      <Descriptions size="small" bordered column={{ xs: 1, sm: 2, md: 4 }} style={{ marginTop: 16, fontVariantNumeric: 'tabular-nums' }} items={cells.map(c => ({ key: c.key, label: c.label, children: <span data-testid={`metric-${c.key}`}>{c.value}</span> }))} />
      <Typography.Paragraph style={{ marginTop: 16 }}>成功评估图片 {number(metrics?.images)}；确认负样本 {number(metrics?.negative_images)}；发生误检的负样本 {number(metrics?.false_positive_negative_images)}。</Typography.Paragraph>
      <Typography.Title level={4}>分类别指标</Typography.Title>
      <Table rowKey="label" size="small" dataSource={metrics?.per_class?.map(row=>({...row,ap50:run?.metrics?.per_class?.find(r=>r.label===row.label)?.ap50,ap50_95:run?.metrics?.per_class?.find(r=>r.label===row.label)?.ap50_95})) || []} scroll={{ x: 1120 }} pagination={false} locale={{ emptyText: '尚无分类别指标' }} columns={[
        { title: '类别', dataIndex: 'label', width: 150 }, { title: '真值数', dataIndex: 'truth_count', render: number }, { title: '预测数', dataIndex: 'prediction_count', render: number },
        ...(['tp', 'fp', 'fn'] as const).map(key => ({ title: key.toUpperCase(), dataIndex: key, render: number })),
        ...(['precision', 'recall', 'f1', 'ap50', 'ap50_95'] as const).map(key => ({ title: ({ precision: 'Precision', recall: 'Recall', f1: 'F1', ap50: '标准 AP50', ap50_95: '标准 AP50:95' })[key], dataIndex: key, render: ratio })),
      ]} />
      <Typography.Title level={4}>混淆矩阵</Typography.Title>
      <Typography.Paragraph type="secondary">行是真值类别，列是预测类别；背景行/列分别表示误检/漏检。</Typography.Paragraph>
      <Table rowKey="key" size="small" pagination={false} scroll={{ x: Math.max(500, 160 + (matrix?.labels.length || 0) * 100) }}
        dataSource={matrix?.labels.map((label, index) => ({ key: index, label, values: matrix.matrix[index] })) || []}
        columns={[{ title: '真值 ↓ / 预测 →', dataIndex: 'label', width: 160 }, ...(matrix?.labels.map((label, i) => ({ title: label, key: String(i), width: 100, render: (_: unknown, row: { values: number[] }) => number(row.values?.[i]) })) || [])]} locale={{ emptyText: '尚无混淆矩阵' }} />
    </Card>
    {run && <ValidationItems key={viewKey} run={run} view={view} />}
  </Space>;
}
