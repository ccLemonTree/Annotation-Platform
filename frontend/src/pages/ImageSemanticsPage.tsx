import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Input, InputNumber, Pagination, Progress, Radio, Select, Space, Table, Tag, Typography } from 'antd';
import axios from 'axios';
import { getImageCollections, getImages, type ImageCollection, type LibraryImage } from '../api/images';
import { getModelServices, type ModelService } from '../api/modelServices';
import { createSemanticJob, getSemanticJob, getSemanticJobs, searchSemantics, semanticJobAction,
  type SemanticDetail, type SemanticJob, type SemanticResults, type SemanticStatus } from '../api/imageSemantics';
import AssetImage from '../components/AssetImage';

const statusText: Record<SemanticStatus, string> = { pending: '等待中', running: '生成中', completed: '完成', failed: '失败', cancelled: '已取消' };
function errorText(error: unknown) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : '操作失败，请重试；原图已保留';
}

export default function ImageSemanticsPage() {
  const [collections, setCollections] = useState<ImageCollection[]>([]), [models, setModels] = useState<ModelService[]>([]);
  const [collection, setCollection] = useState<string>(), [captionModel, setCaptionModel] = useState<string>(), [embeddingModel, setEmbeddingModel] = useState<string>();
  const [imageData, setImageData] = useState<{ key: string; items: LibraryImage[]; total: number }>(), [imagePage, setImagePage] = useState(1);
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [captionPrompt, setCaptionPrompt] = useState('');
  const [jobs, setJobs] = useState<SemanticJob[]>([]), [jobsPage, setJobsPage] = useState(1), [jobId, setJobId] = useState<string>();
  const [jobDetail, setDetail] = useState<SemanticDetail>(), [itemPage, setItemPage] = useState(1);
  const [mode, setMode] = useState<'text' | 'image'>('text'), [text, setText] = useState(''), [queryImage, setQueryImage] = useState<string>(), [topK, setTopK] = useState(20);
  const [results, setResults] = useState<SemanticResults>(), [searching, setSearching] = useState(false), [working, setWorking] = useState(false);
  const [error, setError] = useState(''), [info, setInfo] = useState('');
  const searchRequest = useRef(0), searchController = useRef<AbortController | null>(null), mounted = useRef(true);
  const [refresh, setRefresh] = useState(0);
  const imageKey = `${collection || ''}:${imagePage}:${refresh}`;
  const imageLoading = imageData?.key !== imageKey;
  const images = imageLoading ? [] : imageData!.items, imageTotal = imageLoading ? 0 : imageData!.total;
  const detail = jobDetail?.id === jobId && jobDetail?.page === itemPage ? jobDetail : undefined;
  const invalidateSearch = () => { searchRequest.current++; searchController.current?.abort(); setResults(undefined); setSearching(false); };

  useEffect(() => {
    mounted.current = true;
    let active = true;
    Promise.all([getImageCollections(), getModelServices({ enabled: true })]).then(([groups, services]) => {
      if (active) { setCollections(groups); setModels(services); }
    }).catch(error => { if (active) setError(errorText(error)); });
    const abortSearch = () => { searchRequest.current++; searchController.current?.abort(); };
    return () => { active = false; mounted.current = false; abortSearch(); };
  }, []);
  useEffect(() => {
    let active = true;
    getImages({ collection_id: collection, page: imagePage, page_size: 20 }).then(data => {
      if (active) setImageData({ key: imageKey, items: data.items, total: data.total });
    }).catch(error => { if (active) { setImageData({ key: imageKey, items: [], total: 0 }); setError(errorText(error)); } });
    return () => { active = false; };
  }, [collection, imagePage, imageKey]);
  useEffect(() => {
    const controller = new AbortController(); let inFlight = false;
    const load = async () => {
      if (inFlight) return; inFlight = true;
      try { const rows = await getSemanticJobs(collection, jobsPage, controller.signal); if (!controller.signal.aborted) setJobs(rows); }
      catch (error) { if (!controller.signal.aborted) setError(errorText(error)); }
      finally { inFlight = false; }
    };
    void load(); const timer = window.setInterval(load, 3000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [collection, jobsPage, refresh]);
  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController(); let inFlight = false;
    const load = async () => {
      if (inFlight) return; inFlight = true;
      try { const row = await getSemanticJob(jobId, itemPage, controller.signal); if (!controller.signal.aborted) setDetail(row); }
      catch (error) { if (!controller.signal.aborted) setError(errorText(error)); }
      finally { inFlight = false; }
    };
    void load(); const timer = window.setInterval(load, 3000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [jobId, itemPage, refresh]);
  const modelOptions = (kind: 'caption' | 'embedding') => models.filter(model => model.kind === kind).map(model => ({ value: model.id, label: `${model.name} · r${model.revision}` }));
  const create = async (wholeCollection = false) => {
    if (working || (!captionModel && !embeddingModel) || (wholeCollection ? !collection : !selected.length)) return;
    setWorking(true); setError(''); setInfo('');
    try {
      const job = await createSemanticJob({ collection_id: collection, image_ids: wholeCollection ? undefined : selected.map(String), caption_model_id: captionModel, embedding_model_id: embeddingModel,
        ...(captionModel && captionPrompt.trim() ? { prompt: captionPrompt.trim() } : {}) });
      if (!mounted.current) return;
      setJobId(job.id); setItemPage(1); setSelected([]); setJobsPage(1); setRefresh(value => value + 1); setInfo(`已创建持久批次，共 ${job.total} 张图片`);
    } catch (error) { if (mounted.current) setError(errorText(error)); }
    finally { if (mounted.current) setWorking(false); }
  };
  const action = async (action: 'cancel' | 'retry', itemId?: string) => {
    if (!jobId || working) return;
    setWorking(true); setError('');
    try { const result = await semanticJobAction(jobId, action, itemId ? [itemId] : undefined);
      if (mounted.current) { setInfo(`已处理 ${result.changed_count} 项`); setRefresh(value => value + 1); }
    } catch (error) { if (mounted.current) setError(errorText(error)); }
    finally { if (mounted.current) setWorking(false); }
  };
  const search = async () => {
    if (!embeddingModel || searching || (mode === 'text' ? !text.trim() : !queryImage)) return;
    const current = ++searchRequest.current;
    searchController.current?.abort(); searchController.current = new AbortController();
    setSearching(true); setError(''); setResults(undefined);
    try { const result = await searchSemantics({ embedding_model_id: embeddingModel, collection_id: collection, top_k: topK,
      ...(mode === 'text' ? { text: text.trim() } : { image_id: queryImage }) }, searchController.current.signal);
      if (current === searchRequest.current) setResults(result);
    } catch (error) { if (current === searchRequest.current) setError(errorText(error)); }
    finally { if (current === searchRequest.current) setSearching(false); }
  };
  const done = detail ? detail.total - (detail.counts.pending || 0) - (detail.counts.running || 0) : 0;
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <Card title="图片描述与向量检索" extra={<Button onClick={() => setRefresh(value => value + 1)}>刷新</Button>}>
      <Alert type="info" showIcon title="完整2048维向量按模型、配置版本和预处理隔离，首版精确检索每个当前范围最多5000条索引。超限会明确报错，请缩小图片集。相似度仅用于检索，不代表标注正确性。" />
      {error && <Alert style={{ marginTop: 12 }} type="error" showIcon title={error} />}
      {info && <Typography.Paragraph role="status">{info}</Typography.Paragraph>}
      <Space wrap style={{ marginTop: 16, width: '100%' }} styles={{ item: { maxWidth: '100%' } }}>
        <Select aria-label="图片集筛选" allowClear placeholder="全部图片" value={collection} style={{ width: 220, maxWidth: '100%' }} disabled={working}
          options={collections.map(group => ({ value: group.id, label: `${group.name} (${group.image_count})` }))}
          onChange={value => { invalidateSearch(); setCollection(value); setImagePage(1); setSelected([]); setQueryImage(undefined); setJobs([]); setJobsPage(1); setJobId(undefined); }} />
        <Select aria-label="描述模型" allowClear placeholder="选择描述模型（可选）" value={captionModel} style={{ width: 240, maxWidth: '100%' }} options={modelOptions('caption')} onChange={setCaptionModel} disabled={working} />
        <Select aria-label="向量模型" allowClear placeholder="选择Embedding模型" value={embeddingModel} style={{ width: 240, maxWidth: '100%' }} options={modelOptions('embedding')}
          onChange={value => { invalidateSearch(); setEmbeddingModel(value); }} disabled={working} />
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>模型来自模型管理；至少选择一种模型后生成。更改模型配置后需新建批次，旧版本索引不会混入检索。取消不会删除原图或已完成结果。</Typography.Paragraph>
      <Input.TextArea aria-label="本批描述提示词" placeholder="本批描述提示词（可选）：留空使用模型管理中的描述核心提示词，填写后覆盖本批描述指令。" value={captionPrompt} onChange={event => setCaptionPrompt(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 2, maxRows: 8 }} disabled={working || !captionModel} style={{ marginBottom: 20 }} />
      <Space wrap style={{ marginBottom: 12 }}>
        <Button type="primary" loading={working} disabled={working || !selected.length || (!captionModel && !embeddingModel)} onClick={() => create()}>生成所选 {selected.length} 张</Button>
        <Button disabled={working || !collection || (!captionModel && !embeddingModel)} onClick={() => create(true)}>生成当前图片集</Button>
      </Space>
      <Table<LibraryImage> rowKey="id" loading={imageLoading} dataSource={images} scroll={{ x: 500 }}
        rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: setSelected, getCheckboxProps: () => ({ disabled: working }) }}
        pagination={{ current: imagePage, total: imageTotal, pageSize: 20, showSizeChanger: false, onChange: setImagePage }} columns={[
          { title: '图片', width: 90, render: (_, image) => <AssetImage imageId={image.id} width={64} height={48} alt={image.original_name} style={{ objectFit: 'contain' }} /> },
          { title: '文件名', dataIndex: 'original_name' }, { title: '尺寸', render: (_, image) => `${image.width ?? '?'} × ${image.height ?? '?'}` },
        ]} />
    </Card>
    <Card title="文本与以图搜图">
      <Space wrap style={{ width: '100%' }} styles={{ item: { maxWidth: '100%' } }}>
        <Radio.Group aria-label="检索方式" value={mode} options={[{ value: 'text', label: '文本检索' }, { value: 'image', label: '以图搜图' }]} onChange={event => { invalidateSearch(); setMode(event.target.value); }} />
        {mode === 'text' ? <Input aria-label="检索文本" value={text} maxLength={2000} placeholder="描述希望查找的内容" style={{ width: 300, maxWidth: '100%' }} onChange={event => { invalidateSearch(); setText(event.target.value); }} onPressEnter={search} /> :
          <Select aria-label="查询图片" value={queryImage} placeholder="选择当前页图片" style={{ width: 300, maxWidth: '100%' }} options={images.map(image => ({ value: image.id, label: image.original_name }))} onChange={value => { invalidateSearch(); setQueryImage(value); }} />}
        <InputNumber aria-label="返回数量TopK" min={1} max={100} precision={0} value={topK} onChange={value => { invalidateSearch(); setTopK(value || 20); }} />
        <Button type="primary" loading={searching} disabled={!embeddingModel || searching || (mode === 'text' ? !text.trim() : !queryImage)} onClick={search}>检索</Button>
      </Space>
      {results && <><Typography.Paragraph role="status" style={{ marginTop: 12 }}>当前索引 {results.indexed_count} / {results.limit} 条 · r{results.space.revision} · {results.space.dimensions}维。{results.warning}</Typography.Paragraph>
        <Table rowKey="image_id" dataSource={results.items} pagination={false} scroll={{ x: 700 }} locale={{ emptyText: '当前索引没有匹配图片，请先生成向量' }} columns={[
          { title: '图片', render: (_, image) => <AssetImage imageId={image.image_id} width={100} height={72} alt={image.original_name} style={{ objectFit: 'contain' }} /> },
          { title: '文件名', dataIndex: 'original_name' }, { title: '描述', dataIndex: 'caption' },
          { title: '余弦相似度', dataIndex: 'score', render: (value: number) => value.toFixed(4) },
        ]} /></>}
    </Card>
    <Card title="持久生成队列">
      <Table<SemanticJob> rowKey="id" dataSource={jobs} pagination={false} scroll={{ x: 600 }} columns={[
        { title: '批次', dataIndex: 'id', render: (value: string) => <Button type="link" onClick={() => { setJobId(value); setItemPage(1); }}>{value.slice(0, 8)}</Button> },
        { title: '总计', dataIndex: 'total' }, ...(['pending', 'running', 'completed', 'failed', 'cancelled'] as SemanticStatus[]).map(status => ({ title: statusText[status], key: status, render: (_: unknown, job: SemanticJob) => job.counts[status] || 0 })),
      ]} />
      <Space><Button disabled={jobsPage === 1} onClick={() => setJobsPage(value => value - 1)}>上一批</Button><span>第 {jobsPage} 页</span><Button disabled={jobs.length < 20} onClick={() => setJobsPage(value => value + 1)}>下一批</Button></Space>
      {detail && <>
        <Typography.Title level={5}>批次 {detail.id.slice(0, 8)} · 已处理 {done} / {detail.total}</Typography.Title>
        <Progress percent={detail.total ? Math.round(done / detail.total * 100) : 0} status={detail.counts.failed ? 'exception' : undefined} />
        <Space style={{ marginBottom: 12 }}>
          <Button danger disabled={working || !(detail.counts.pending || detail.counts.running)} onClick={() => action('cancel')}>取消未完成项</Button>
          <Button disabled={working || !(detail.counts.failed || detail.counts.cancelled)} onClick={() => action('retry')}>重试失败与取消项</Button>
        </Space>
        <Table rowKey="id" dataSource={detail.items} pagination={false} scroll={{ x: 700 }} columns={[
          { title: '图片', dataIndex: 'original_name' }, { title: '状态', dataIndex: 'status', render: (value: SemanticStatus) => <Tag>{statusText[value]}</Tag> },
          { title: '描述', dataIndex: 'caption' }, { title: '失败原因', dataIndex: 'error' },
          { title: '操作', render: (_, item) => <Space>
            <Button size="small" danger disabled={working || !['pending', 'running'].includes(item.status)} onClick={() => action('cancel', item.id)}>取消此项</Button>
            <Button size="small" disabled={working || !item.image_id || !['failed', 'cancelled'].includes(item.status)} onClick={() => action('retry', item.id)}>重试此项</Button>
          </Space> },
        ]} />
        <Pagination current={itemPage} pageSize={20} total={detail.total} showSizeChanger={false} onChange={setItemPage} style={{ marginTop: 12 }} />
      </>}
    </Card>
  </Space>;
}
