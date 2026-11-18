import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Pagination, Popconfirm, Segmented, Select, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import api from '../api/client';
import { getTrainingServers } from '../api/training';
import { getGpuResources, type GpuResource, type GpuServer } from '../api/gpuResources';
import { getImageCollections, type ImageCollection } from '../api/images';
import { getAssets } from '../api/assets';
import type { Project } from '../api/projects';
import { getModelServices, isModelOffline, hasDetectionConfidence, type ModelService } from '../api/modelServices';
import { validationError } from '../api/validation';
import AssetImage from '../components/AssetImage';
import ImageUploadDialog from '../components/ImageUploadDialog';
import TrainingLogs from '../components/TrainingLogs';

type Dataset = { id: string; name: string };
type Source = Dataset & { server_id: string };
type Model = Dataset & { server: string; gpu: string | null; port: number; state: string; error?: string };
type Detection = { id:string; name:string; counts:Record<string,number>; labels:string[]; confidence:number };
type Indexes = { total: number; originals?:number; items: { model_id: string; complete: number; failed: number; error?: string }[]; detection?:Detection };
type TestImage = {id:string; original_name:string; source_type:string; device_name?:string};
type Result = { candidates: number; results: (Dataset & { hits: { asset_id: string; score: number }[] })[] };
type Deploy = { run_id: string; server_id: string; gpu: string; port: number };

const root = '/api/vector-test';
const states: Record<string, string> = {queued:'等待部署',pulling:'拉取镜像中',checking:'启动检查中',ready:'已就绪',failed:'需处理',stopping:'停止中',stopped:'已停止'};

function TestImages({dataset,isolated,targets,refresh,onClose}:{dataset:Dataset;isolated:boolean;targets:boolean;refresh:number;onClose:()=>void}) {
  const [images,setImages]=useState<TestImage[]>();
  const [page,setPage]=useState(1);
  const [total,setTotal]=useState(0);
  const [error,setError]=useState('');
  const [retry,setRetry]=useState(0);
  const imagesUrl=`${root}/datasets/${dataset.id}/images`;
  useEffect(()=>{
    let active=true;
    const request=isolated?api.get<{items:TestImage[];total:number}>(imagesUrl,{params:{page,targets}}).then(response=>response.data):
      getAssets({project_id:dataset.id,image_kind:'originals',page,page_size:12});
    request.then(data=>{if(active){setImages(data.items);setTotal(data.total);}})
      .catch(cause=>{if(active)setError(validationError(cause,'读取图片失败'));});
    return ()=>{active=false;};
  },[dataset.id,imagesUrl,isolated,targets,page,retry,refresh]);
  return <Drawer open title={targets?'测试目标图':isolated?'测试原图':'原图'} onClose={onClose} size={640}
    extra={!isolated&&<Link to={`/projects/${dataset.id}/images`}>查看全部图片</Link>}>
    {error?<Alert type="error" showIcon title={error} action={<Button size="small" onClick={()=>{
      setError('');setRetry(value=>value+1);
    }}>重试</Button>}/>:!images?<Spin/>:images.length===0?<Empty description={targets?'还没有测试目标图':'还没有原图'}/>:
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(min(100%,130px),1fr))',gap:12}}>
        {images.map(asset=><Space key={asset.id} orientation="vertical" size={4} style={{width:'100%',minWidth:0}}>
          <AssetImage assetId={asset.id} contentUrl={isolated?`${imagesUrl}/${asset.id}/content`:undefined} alt={asset.original_name} width="100%" height={102} style={{objectFit:'contain'}}/>
          <Typography.Text ellipsis={{tooltip:asset.original_name}} style={{maxWidth:'100%'}}>{asset.original_name}</Typography.Text>
          {isolated&&<Typography.Text type="secondary" ellipsis title={asset.device_name}>
            {asset.device_name||({upload:'上传',smarteye:'定时抓图',model_crop:'检测切图'} as Record<string,string>)[asset.source_type]}
          </Typography.Text>}
        </Space>)}
      </div>}
    {total>12&&<Pagination style={{marginTop:16}} current={page} total={total} pageSize={12} showSizeChanger={false}
      onChange={value=>{setPage(value);setImages(undefined);setError('');}}/>}
  </Drawer>;
}

function DetectionForm({detection,busy,onStart}:{detection?:Detection;busy:boolean;
  onStart:(values:{model_id:string;labels?:string[];confidence?:number})=>void}) {
  const [services,setServices]=useState<ModelService[]>([]);
  const [error,setError]=useState('');
  const [form]=Form.useForm();
  const chosen=Form.useWatch('model_id',form);
  const model=services.find(row=>row.id===chosen);
  useEffect(()=>{
    let active=true;
    getModelServices({kind:'detection',enabled:true}).then(rows=>{if(active)setServices(rows);})
      .catch(cause=>{if(active)setError(validationError(cause,'读取检测模型失败'));});
    return ()=>{active=false;};
  },[]);
  return <Space orientation="vertical" size="middle" style={{width:'100%'}}>
    {error&&<Alert type="error" showIcon title={error}/>}
    {detection&&<Alert type="info" showIcon title={'当前检测模型：'+detection.name}
      description={`原图已处理 ${detection.counts.completed??0} 张，待处理 ${(detection.counts.pending??0)+(detection.counts.running??0)} 张，失败 ${detection.counts.failed??0} 张。新入库原图会自动检测。`}/>}
    <Typography.Text type="secondary">每个检测框裁成一张目标图。启用后，检索索引只收录这条流程产生的目标图。</Typography.Text>
    <Form form={form} layout="vertical" disabled={busy} onFinish={onStart}>
      <Form.Item name="model_id" label="检测模型" rules={[{required:true,message:'请选择检测模型'}]}>
        <Select optionFilterProp="label" showSearch options={services.map(row=>({value:row.id,label:row.name,disabled:isModelOffline(row)}))}
          onChange={()=>form.setFieldsValue({labels:[],confidence:undefined})}/>
      </Form.Item>
      <Form.Item name="labels" label="目标类别" extra="留空时使用模型已配置的类别。输入后按回车添加。">
        <Select mode="tags" tokenSeparators={[',','，']} options={model?.labels.map(label=>({value:label,label}))}/>
      </Form.Item>
      {model&&hasDetectionConfidence(model)&&<Form.Item name="confidence" label="检测置信度" extra="留空时使用模型默认值。">
        <InputNumber min={0} max={1} step={0.05} placeholder={String(model.confidence)}/>
      </Form.Item>}
      <Button type="primary" htmlType="submit" loading={busy}>{detection?'更新配置并重新检测':'开始检测并切图'}</Button>
    </Form>
  </Space>;
}

export default function VectorSearchTestPage({embeddedProject}: {embeddedProject?: Project}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [chosenDataset, setDatasetId] = useState<string>();
  const datasetId = embeddedProject?.id ?? chosenDataset;
  const [models, setModels] = useState<Model[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [servers, setServers] = useState<GpuServer[]>([]);
  const [gpus, setGpus] = useState<GpuResource[]>([]);
  const [collections, setCollections] = useState<ImageCollection[]>([]);
  const [indexes, setIndexes] = useState<Indexes>();
  const [modelIds, setModelIds] = useState<string[]>([]);
  const [logs, setLogs] = useState<{ id: string; name: string; content: string; error?: string }>();
  const [name, setName] = useState('');
  const [query, setQuery] = useState('');
  const [image, setImage] = useState<File>();
  const [mode, setMode] = useState<'text' | 'image'>('text');
  const [topK, setTopK] = useState(10);
  const [result, setResult] = useState<Result>();
  const [upload, setUpload] = useState(false);
  const [imageView, setImageView] = useState<'originals'|'targets'>();
  const [capture, setCapture] = useState(false);
  const [uploadStatus,setUploadStatus] = useState('');
  const fileInput=useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState('search');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [form] = Form.useForm<Deploy>();
  const serverId = Form.useWatch('server_id', form);
  const dataset = embeddedProject ? {id:embeddedProject.id,name:embeddedProject.project_name} : datasets.find(row => row.id === datasetId);
  const isolated=!!embeddedProject;
  const datasetUrl=`${root}/datasets/${datasetId}`;
  const scope=isolated?'?isolated=true':'';

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([embeddedProject ? undefined : api.get<Dataset[]>(root+'/datasets',{signal:controller.signal}),
      api.get<Source[]>(root+'/sources',{signal:controller.signal}), getTrainingServers(controller.signal)])
      .then(([data, training, available]) => {
        if (data) {
          setDatasets(data.data);
          setDatasetId(id => id ?? data.data[0]?.id);
        }
        setSources(training.data);
        setServers(available.filter(server => server.enabled && server.id !== 'default' && server.has_password));
      }).catch(e => { if (!controller.signal.aborted) setError(validationError(e,'读取数据集、训练产物或服务器失败')); });
    return () => controller.abort();
  }, [embeddedProject, refresh]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const [deploys, progress] = await Promise.all([
          api.get<Model[]>(root+'/models',{signal:controller.signal}),
          datasetId ? api.get<Indexes>(datasetUrl+'/indexes'+scope,{signal:controller.signal}) : undefined,
        ]);
        if (active) { setModels(deploys.data); setIndexes(progress?.data); }
      } catch (e) {
        if (active) setError(previous => previous || validationError(e,'更新模型状态或索引进度失败'));
      } finally {
        if (active) timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [datasetId, datasetUrl, scope, refresh]);

  useEffect(() => {
    if (!serverId) return;
    const controller = new AbortController();
    getGpuResources(controller.signal, serverId).then(x => setGpus(x.gpus)).catch(e => {
      if (!controller.signal.aborted) setError(validationError(e,'读取显卡失败'));
    });
    return () => controller.abort();
  }, [serverId]);

  async function act(key: string, action: () => Promise<void>) {
    setBusy(key); setError('');
    try { await action(); } catch (e) { setError(validationError(e,'操作失败，请检查后重试')); }
    finally { setBusy(''); }
  }

  function selectDataset(id: string) {
    setDatasetId(id); setIndexes(undefined); setModelIds([]); setResult(undefined);
  }

  function uploadTestImages(files:File[]) {
    if (!files.length) return;
    void act('upload',async()=>{
      let added=0,duplicate=0,invalid=0;
      const failures:string[]=[];
      for (const [index,file] of files.entries()) {
        setUploadStatus(`正在导入 ${index+1} / ${files.length} 个文件`);
        const input=new FormData();
        input.append('file',file);
        try {
          const {data}=await api.post<{added:number;duplicate:number;invalid:number}>(datasetUrl+'/images',input,
            {timeout:0,headers:{'Content-Type':undefined},skipErrorToast:true});
          added+=data.added;duplicate+=data.duplicate;invalid+=data.invalid;
        }catch(e){failures.push(`${file.name}：${validationError(e,'导入失败')}`);}
      }
      setUploadStatus(`已导入 ${added} 张，重复 ${duplicate} 张，无效 ${invalid} 张${failures.length?`，失败 ${failures.length} 个文件`:''}`);
      if (failures.length) setError(failures.join('；'));
      setRefresh(n=>n+1);
    });
  }

  async function readLogs(id: string) {
    setBusy('logs');
    try {
      const {data} = await api.get<{logs:string}>(root+'/models/'+id+'/logs',{timeout:45000,skipErrorToast:true});
      setLogs(current => current?.id === id ? {...current,content:data.logs,error:undefined} : current);
    } catch (cause) {
      setLogs(current => current?.id === id ? {...current,error:validationError(cause,'读取日志失败')} : current);
    } finally {
      setBusy('');
    }
  }

  const indexed = new Map(indexes?.items.map(row => [row.model_id, row]));
  const selectable = models.filter(model => model.state === 'ready' && (indexed.get(model.id)?.complete ?? 0) > 0);
  const selected = modelIds.filter(id => selectable.some(model => model.id === id));
  const search = () => act('search', async () => {
    if (!dataset || !selected.length) return;
    setResult(undefined);
    const input = new FormData();
    selected.forEach(id => input.append('model_ids', id));
    input.append('top_k', String(topK));
    if (mode === 'image' && image) input.append('image', image);
    if (mode === 'text') input.append('text', query.trim());
    setResult((await api.post<Result>(datasetUrl+'/search'+scope,input,{
      timeout:160000, headers:{'Content-Type':undefined},
    })).data);
  });

  const dataControls = <Space wrap>
    {!embeddedProject && <>
    <Select aria-label="测试数据集" placeholder="选择测试数据集" value={datasetId} onChange={selectDataset} disabled={!!busy}
      style={{minWidth:200}} options={datasets.map(row => ({value:row.id,label:row.name}))}/>
    <Input aria-label="新建测试数据集名称" placeholder="新数据集名称" value={name} maxLength={200}
      style={{width:185}} onChange={e => setName(e.target.value)}/>
    <Button disabled={!name.trim() || !!busy} loading={busy === 'dataset'} onClick={() => void act('dataset', async () => {
      const {data} = await api.post<Dataset>(root+'/datasets',{name:name.trim()});
      setDatasets(rows => [data, ...rows]); selectDataset(data.id); setName('');
    })}>新建数据集</Button>
    </>}
    {dataset && <>
      {isolated&&<input hidden ref={fileInput} type="file" aria-label="选择测试图片或压缩包" multiple
        accept=".jpg,.jpeg,.png,.bmp,.webp,.zip,.tar,.tar.gz,.tgz" onChange={e=>{
          uploadTestImages(Array.from(e.target.files||[]));e.target.value='';
        }}/>}
      <Button type="primary" disabled={!!busy} loading={busy==='upload'} onClick={() => isolated?fileInput.current?.click():void act('upload', async () => {
        setCollections(await getImageCollections()); setUpload(true);
      })}>上传图片或压缩包</Button>
      {isolated?<Button onClick={()=>setCapture(true)}>定时抓图</Button>:<Link to={'/projects/'+dataset.id+'/capture'}>定时抓图 / 数据拉取</Link>}
      <Button onClick={()=>setImageView('originals')}>{isolated?'查看测试原图':'查看原图'}</Button>
      {indexes?.detection && (isolated?<Button onClick={()=>setImageView('targets')}>查看测试目标图</Button>:
        <Link to={`/projects/${dataset.id}/images?work_job_id=${indexes.detection.id}`}>查看目标图</Link>)}
    </>}
  </Space>;

  return <Space orientation="vertical" size="large" style={{width:'100%', minWidth:0}}>
    {error && <Alert showIcon type="error" title={error} closable onClose={() => setError('')}/>}
    <section aria-label="测试数据">
      {dataControls}
      {dataset && <Typography.Paragraph type="secondary" style={{margin:'8px 0 0'}} aria-live="polite">
        {indexes ? isolated?`测试原图 ${indexes.originals??0} 张${indexes.detection?`，可索引目标图 ${indexes.total} 张`:''}`:
          indexes.detection?`已生成 ${indexes.total} 张可索引目标图`:`数据集共 ${indexes.total} 张原图` : '正在读取图片进度…'}
      </Typography.Paragraph>}
      {isolated && <Typography.Paragraph type="secondary" style={{margin:'4px 0 0'}}>这里上传和拉取的图片仅用于本项目的向量检索测试。</Typography.Paragraph>}
      {uploadStatus&&<span role="status">{uploadStatus}</span>}
    </section>
    <Tabs activeKey={tab} onChange={setTab} items={[
      {key:'search',label:'检索与对比',children:<Space orientation="vertical" style={{width:'100%'}} size="middle">
        {dataset && indexes && selectable.length===0 && <Alert type="info" showIcon title="完成索引后就能检索"
          description={<Space wrap>
            {!indexes.detection && <Button size="small" onClick={()=>setTab('detection')}>先检测并切图</Button>}
            <Button size="small" onClick={()=>setTab('deploy')}>{indexes.detection?'查看目标图索引':'查看索引设置'}</Button>
          </Space>}/>}
        <Form layout="vertical" onFinish={() => void search()}>
          <Form.Item label="对比模型" extra="最多选 4 个模型，同一次检索使用共同已索引的候选图片。">
            <Select mode="multiple" maxCount={4} aria-label="对比模型" value={selected} onChange={ids => {setModelIds(ids);setResult(undefined);}}
              disabled={!!busy} placeholder="选择已就绪且已建索引的模型"
              options={selectable.map(model => ({value:model.id,label:model.name+' · '+model.server+':'+model.port}))}/>
          </Form.Item>
          <Segmented<'text' | 'image'> options={[{value:'text',label:'文搜图'},{value:'image',label:'图搜图'}]} value={mode}
            disabled={!!busy} onChange={value => {setMode(value);setResult(undefined);}}/>
          <Form.Item label={mode === 'text' ? '查询文本' : '查询图片'} style={{marginTop:16}}
            extra={mode==='image'&&indexes?.detection?'建议上传只包含待检索目标的图片。':undefined}>
            {mode === 'text' ? <Input.TextArea aria-label="查询文本" rows={2} maxLength={2000} disabled={!!busy}
              value={query} onChange={e => {setQuery(e.target.value);setResult(undefined);}}/>
              : <input aria-label="查询图片" type="file" accept="image/jpeg,image/png,image/bmp,image/webp" disabled={!!busy}
                onChange={e => {setImage(e.target.files?.[0]);setResult(undefined);}}/>}
          </Form.Item>
          <Space wrap>
            <label htmlFor="vector-test-top-k">每个模型返回</label>
            <InputNumber id="vector-test-top-k" min={1} max={30} precision={0} value={topK}
              onChange={value => setTopK(value ?? 10)} disabled={!!busy}/>
            <Button type="primary" htmlType="submit" loading={busy === 'search'}
              disabled={!dataset || !selected.length || !!busy || (mode === 'text' ? !query.trim() : !image)}>开始检索</Button>
          </Space>
        </Form>
        {result ? <>
          <Typography.Text strong>{result.candidates} 张共同候选图片</Typography.Text>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,245px),1fr))',gap:16}}>
            {result.results.map(column => <section key={column.id} aria-label={column.name}>
              <Typography.Title level={5}>{column.name}</Typography.Title>
              <div style={{display:'grid',gap:16,gridTemplateColumns:result.results.length===1?
                'repeat(auto-fill,minmax(min(100%,280px),1fr))':'minmax(0,1fr)'}}>
                {column.hits.map(hit => <figure key={hit.asset_id} style={{minWidth:0,margin:0}}>
                  <div style={{aspectRatio:'4 / 3',background:'var(--ui-surface)',borderRadius:8,overflow:'hidden'}}>
                    <AssetImage contentUrl={`${datasetUrl}/images/${hit.asset_id}/preview${scope}`} alt="检索命中图片"
                      width="100%" height="100%" style={{objectFit:'contain'}}/>
                  </div>
                  <figcaption style={{marginTop:6}}><Typography.Text>相似度 {hit.score.toFixed(4)}</Typography.Text></figcaption>
                </figure>)}
              </div>
            </section>)}
          </div>
        </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={selectable.length ? '提交查询后查看各模型的检索结果' :
            indexes?.detection&&indexes.total===0?
              (indexes.detection.counts.completed && !indexes.detection.counts.pending && !indexes.detection.counts.running
                ? '当前没有检出的目标图，请检查「检测与切图」中的进度和配置' : '等待检测流程生成目标图')
              :'请在「测试模型与索引」部署模型并为当前数据建索引'}/>}
      </Space>},
      {key:'detection',label:'检测与切图',children:dataset?<DetectionForm key={dataset.id} detection={indexes?.detection} busy={busy==='detection'}
        onStart={values=>void act('detection',async()=>{
          await api.post(datasetUrl+'/detection'+scope,values);
          setResult(undefined);setIndexes(undefined);setRefresh(n=>n+1);
        })}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请先选择测试数据集"/>},
      {key:'deploy',label:'测试模型与索引',children:<Space orientation="vertical" style={{width:'100%'}} size="middle">
        <Form form={form} layout="vertical" initialValues={{port:8001}} onFinish={values => void act('deploy', async () => {
          await api.post(root+'/models',values); setRefresh(n => n+1);
        })}>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,160px),1fr))',gap:12}}>
            <Form.Item name="run_id" label="已合并的 WeMM 模型" rules={[{required:true}]}>
              <Select showSearch optionFilterProp="label" options={sources.map(source => ({value:source.id,label:source.name}))}
                onChange={id => {
                  const server = sources.find(row => row.id === id)?.server_id;
                  form.setFieldsValue({server_id:servers.some(row => row.id === server) ? server : undefined,gpu:undefined});
                  if (server !== serverId) setGpus([]);
                }}/>
            </Form.Item>
            <Form.Item name="server_id" label="部署服务器" rules={[{required:true}]}>
              <Select options={servers.map(server => ({value:server.id,label:server.name}))}
                onChange={() => {setGpus([]);form.setFieldValue('gpu',undefined);}}/>
            </Form.Item>
            <Form.Item name="gpu" label="空闲 GPU" rules={[{required:true}]}>
              <Select options={gpus.map(gpu => ({value:gpu.uuid,label:'GPU '+gpu.index+' · '+gpu.name,
                disabled:models.some(model => model.gpu === gpu.uuid) || gpu.memory_used_mib !== 0}))}/>
            </Form.Item>
            <Form.Item name="port" label="主机端口" rules={[{required:true,type:'number',min:1024,max:65535}]}>
              <InputNumber min={1024} max={65535} precision={0} style={{width:'100%'}}/>
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" disabled={!!busy} loading={busy === 'deploy'}>部署测试模型</Button>
        </Form>
        <Table rowKey="id" dataSource={models} size="small" scroll={{x:680}} pagination={{pageSize:10}} columns={[
          {title:'模型',dataIndex:'name'},
          {title:'服务器',render:(_, model) => model.server+':'+model.port},
          {title:'服务状态',render:(_, model) => <><Tag color={model.state === 'ready' ? 'green' : model.state === 'failed' ? 'red' : undefined}>
            {states[model.state] ?? model.state}</Tag>{model.error && <div>{model.error}</div>}</>},
          {title:'当前数据集索引',render:(_, model) => {
            const row = indexed.get(model.id);
            return row ? <>{row.complete}/{indexes?.total}{row.failed > 0 && <div title={row.error}>失败 {row.failed} 张，后台重试</div>}</> : '未建立';
          }},
          {title:'操作',render:(_, model) => <Space wrap>
            {dataset && model.state === 'ready' && !indexed.has(model.id) && <Button size="small" disabled={!!busy} onClick={() => void act('index',async () => {
              await api.put(datasetUrl+'/indexes/'+model.id+scope); setRefresh(n => n+1);
            })}>建立索引</Button>}
            <Button size="small" disabled={!!busy} onClick={() => {
              setLogs({id:model.id,name:model.name,content:''});
              void readLogs(model.id);
            }}>日志</Button>
            {['ready','failed'].includes(model.state) && <Popconfirm title="停止此测试模型？" onConfirm={() => act('stop',async () => {
              await api.post(root+'/models/'+model.id+'/stop'); setRefresh(n => n+1);
            })}><Button size="small" disabled={!!busy}>停止</Button></Popconfirm>}
          </Space>},
        ]}/>
      </Space>},
    ]}/>
    {dataset && imageView && <TestImages key={imageView} dataset={dataset} isolated={isolated} targets={imageView==='targets'}
      refresh={refresh} onClose={()=>setImageView(undefined)}/>}
    {isolated && dataset && <Drawer open={capture} destroyOnHidden size={1280} title="定时抓图 · 向量检索测试" onClose={()=>{
      setCapture(false);setRefresh(n=>n+1);
    }}>
      <iframe title="向量检索测试抓图" className="smarteye-frame" src={`/smarteye/index.html?project_id=${encodeURIComponent(dataset.id)}&vector_test=1`}/>
    </Drawer>}
    <Modal open={!!logs} title={logs&&logs.name+' · 测试日志'} onCancel={() => setLogs(undefined)} footer={null} width={880} destroyOnHidden>
      {logs&&<>{logs.error&&<Alert type="error" showIcon title={logs.error}/>}
        <TrainingLogs key={logs.id} label="测试日志内容" text={logs.content} busy={busy==='logs'}
          onRefresh={()=>void readLogs(logs.id)} note="刷新读取当前服务器日志，错误与告警保留。"/></>}
    </Modal>
    {!isolated && dataset && <ImageUploadDialog key={dataset.id} open={upload} initialName={dataset.name} collections={collections}
      projectId={dataset.id} projectName={dataset.name} taskType="vector"
      onClose={() => {setUpload(false);setRefresh(n => n+1);}} onImported={() => setRefresh(n => n+1)}
      onViewCollection={() => setUpload(false)}/>}
  </Space>;
}
