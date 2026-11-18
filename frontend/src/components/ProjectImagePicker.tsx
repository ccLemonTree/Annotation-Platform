import { useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Drawer, Pagination, Progress, Select, Space, Table, Tag } from 'antd';
import { getImages, getImageCollections, associateImages } from '../api/images';
import type { LibraryImage, ImageCollection, ImageFilters as Filters } from '../api/images';
import AssetImage from './AssetImage';
import ImageFilters from './ImageFilters';
import ImageSelection from './ImageSelection';
import {getProjects,type Project} from '../api/projects';
import {useSceneGroups,sceneLabel} from './SceneCategories';

export default function ProjectImagePicker({ projectId, projectName, taskType='detection', onClose, onAdded }: {
  projectId: string; projectName: string;taskType?:string; onClose: () => void; onAdded: (count: number,summary?:string) => void;
}) {
  const { modal } = App.useApp();
  const [images, setImages] = useState<LibraryImage[]>([]);
  const [collections, setCollections] = useState<ImageCollection[]>([]);
  const [used, setUsed] = useState(new Set<string>());
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [filters, setFilters] = useState<Filters>(taskType==='segmentation'?{}:{review_status:'approved_or_pending'});
  const sceneGroups=useSceneGroups(collections,filters.collection_id);
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1), [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true), [contextLoading, setContextLoading] = useState(true);
  const [projectError, setProjectError] = useState('');
  const [reloadProjects, setReloadProjects] = useState(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [reuseProgress, setReuseProgress] = useState<{done:number;total:number}|null>(null);
  const [includeImported,setIncludeImported]=useState(false);
  const [includeSource,setIncludeSource]=useState(taskType!=='segmentation'),[projects,setProjects]=useState<Project[]>([]);
  const includePending=filters.review_status==='approved_or_pending';
  const source=projects.find(project=>project.id===filters.source_project_id);
  const compatible=!!source&&source.task_type===taskType&&taskType!=='segmentation';
  const changeFilters=(value:Filters)=>{setFilters(value);setPage(1);setSelected([]);};
  useEffect(() => {
    let active = true;
    setContextLoading(true);
    setProjectError('');
    (async () => {
      const projects:Project[]=[];
      for(let page=1;active;page++){
        const result=await getProjects({page,page_size:100});
        projects.push(...result.items);
        if(projects.length>=result.total||!result.items.length)break;
      }
      if (active) setProjects(projects.filter(project=>project.id!==projectId));
    })().catch(() => { if (active) setProjectError('来源项目读取失败'); })
      .finally(() => { if (active) setContextLoading(false); });
    return () => { active = false; };
  }, [projectId,reloadProjects]);
  useEffect(() => {
    const controller=new AbortController();
    getImageCollections(controller.signal).then(setCollections).catch(() => {});
    return () => controller.abort();
  }, []);
  const canList=!includeSource||compatible;
  useEffect(() => {
    setImages([]);setTotal(0);setError('');
    if(!canList){setLoading(false);return;}
    const controller=new AbortController();
    setLoading(true);
    getImages({ ...filters, page, page_size: pageSize, reuse_project_id:projectId },controller.signal)
      .then(result => { if (!controller.signal.aborted) { setImages(result.items); setTotal(result.total); } })
      .catch(() => { if (!controller.signal.aborted) setError('图片读取失败，请重新筛选'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, page, pageSize, projectId, canList]);
  const add = async () => {
    setBusy(true); setError('');
    setReuseProgress({done:0,total:selected.length});
    try {
      const result = await associateImages(projectId, selected.map(String), ids => {
        const done = new Set(ids);
        setReuseProgress(previous => previous && {...previous,done:previous.done+ids.length});
        setSelected(keys => keys.filter(key => !done.has(String(key))));
        setUsed(previous => new Set([...previous, ...ids]));
      },includeImported,filters.source_project_id?{projectId:filters.source_project_id,includeAnnotations:includeSource,includePending}:undefined);
      onAdded(result.added_count,includeSource?`新增引用 ${result.added_count} 张，已有图片 ${result.skipped_count} 张，复制来源标注 ${result.reused_annotations} 张，已有标注保留 ${result.conflicts} 张，无可复制标注 ${result.without_annotations+result.annotation_skipped} 张`:includeImported?`已添加 ${result.added_count} 张图片，带入标注 ${result.annotated}，标注跳过 ${result.annotation_skipped}，冲突保留 ${result.conflicts}`:undefined);
    } catch { setError('添加未全部完成；成功批次已生效，剩余选择已保留，请重试'); }
    finally { setBusy(false);setReuseProgress(null); }
  };
  const confirmAdd=()=>{
    if(includeSource){if(!compatible||!filters.source_project_id){setError('请选择任务类型兼容的来源项目后复制标注');return;}modal.confirm({title:'确认复制来源项目标注',content:`从“${source?.project_name}”复制${includePending?'已通过和待审核':'已通过'}的最新标注到“${projectName}”。待审核标注复制后仍待审核，目标项目已有标注会保留。`,okText:'确认复用',cancelText:'取消',onOk:()=>{void add();}});}
    else void add();
  };
  return <Drawer title={`复用已有数据到 ${projectName}`} open size={1080}
    styles={{ wrapper: { maxWidth: '100vw' }, body: { display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' } }}
    onClose={() => { if (!busy) onClose(); }} footer={<>
      {reuseProgress&&<div role="status">
        已处理 {reuseProgress.done} / {reuseProgress.total} 张
        <Progress percent={Math.floor(reuseProgress.done/reuseProgress.total*100)} status="active" />
      </div>}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <span>已选 {selected.length} 张 · 添加到 {projectName}</span>
        <Space wrap>
          <Button aria-label="取消选图" onClick={onClose} disabled={busy}>取消</Button>
          <Button type="primary" loading={busy} disabled={!selected.length || loading || !canList}
            onClick={confirmAdd}>添加到 {projectName}</Button>
        </Space>
      </div>
    </>}>
    <div style={{ flexShrink: 0, maxHeight: '42%', overflowY: 'auto' }}>
    <p>目标项目：{projectName}。重复图片共用原图，只创建项目引用；各项目标注独立，只带入本项目标签匹配的内容，目标已有标注不覆盖。</p>
    <Space wrap style={{marginBottom:12}}>
      <Select aria-label="复用来源项目" placeholder="选择来源项目" allowClear showSearch optionFilterProp="label" style={{width:260,maxWidth:'100%'}} value={filters.source_project_id}
        loading={contextLoading} notFoundContent={contextLoading?'正在读取项目…':'暂无可选项目'} disabled={busy}
        options={projects.filter(project=>!includeSource||project.task_type===taskType).map(project=>({value:project.id,label:project.project_name}))}
        onChange={id=>changeFilters({...filters,source_project_id:id,source_type:undefined,label:undefined})}/>
      {projectError&&<Button size="small" onClick={()=>setReloadProjects(n=>n+1)}>{projectError}，重试</Button>}
      <Select aria-label="来源标注标签" placeholder="全部目标标签" allowClear showSearch optionFilterProp="label" style={{width:180,maxWidth:'100%'}} value={filters.label} disabled={busy||!source} options={(Array.isArray(source?.label_schema?.labels)?source.label_schema.labels as string[]:[]).map(label=>({value:label,label}))} onChange={label=>changeFilters({...filters,label})}/>
    </Space>
    <Space wrap><Checkbox checked={includeSource} disabled={busy||taskType==='segmentation'} onChange={event=>{
      const checked=event.target.checked;
      setIncludeSource(checked);setIncludeImported(false);
      changeFilters({...filters,...(checked&&source&&!compatible?{source_project_id:undefined,source_type:undefined,label:undefined}:{}),
        review_status:checked?'approved_or_pending':undefined});
    }}>复用有标注的数据</Checkbox>
    {includeSource&&<Checkbox checked={includePending} disabled={busy} onChange={event=>changeFilters({...filters,review_status:event.target.checked?'approved_or_pending':'approved'})}>包含待审核标注</Checkbox>}
    <Checkbox checked={includeImported} disabled={busy||taskType!=='detection'} onChange={event=>{setIncludeImported(event.target.checked);setIncludeSource(false);changeFilters({...filters,review_status:undefined});}}>带入已有YOLO标注</Checkbox></Space>
    {!source&&includeSource&&<p className="workspace-help">选择来源项目后显示可复用的标注图片。</p>}
    {source&&!compatible&&<Alert type="info" showIcon title="来源与目标任务类型不兼容或暂不支持复制；仍可仅复用图片。" style={{marginBottom:12}}/>}
    {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
    <ImageFilters value={filters} collections={collections} groups={sceneGroups} scenes={images.flatMap(image => image.scene_labels)}
      prefix="选择器" disabled={busy} onChange={changeFilters} workflowCrops={!!source} />
    <ImageSelection filters={filters} count={selected.length} projectId={includeImported||includeSource?undefined:projectId} busy={busy} disabled={!canList || loading}
      onBusy={setBusy} onSelect={setSelected} onError={setError} />
    </div>
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
    <Table dataSource={images} rowKey="id" loading={loading} scroll={{ x: 680 }}
      rowSelection={{ selectedRowKeys: selected, onChange: setSelected, preserveSelectedRowKeys: true,
        getCheckboxProps: image => ({ disabled: busy || ((used.has(image.id)||image.in_target_project)&&!includeSource&&(!includeImported||!image.has_imported_annotation)) }) }}
      columns={[
        { title: '预览', width: 80, render: (_, image) => <AssetImage assetId={image.asset_id||undefined} imageId={image.asset_id?undefined:image.id} width={56} height={56} style={{ objectFit: 'cover' }} /> },
        { title: '文件名', dataIndex: 'original_name', width: 230, ellipsis: true },
        { title: '图片集', width: 180, render: (_, image) => <Space wrap>{image.collection_names.map(name => <Tag key={name}>{name}</Tag>)}</Space> },
        { title: '场景', width: 140, render: (_, image) => image.scene_labels.map(scene=>sceneLabel(scene,sceneGroups)).join('、') || '—' },
        {title:'导入标注',width:120,render:(_,image)=>image.has_imported_annotation?<Tag color={image.imported_annotation_trusted?'success':'default'}>{image.imported_annotation_trusted?'已信任':'待审核'}</Tag>:'—'},
        { title: '使用情况', width: 140, render: (_, image) => used.has(image.id)||image.in_target_project ? <Tag>已在当前项目</Tag> : <Tag color="blue">可添加</Tag> },
      ]}
      pagination={false} />
    </div>
    <div style={{ flexShrink: 0, paddingTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
      <Pagination aria-label="图片选择分页" current={page} pageSize={pageSize} total={total} showSizeChanger responsive showLessItems
        pageSizeOptions={[20, 50, 100]} showTotal={count => <span style={{ whiteSpace: 'nowrap' }}>共 {count} 张</span>} disabled={busy}
        onChange={(next, size) => { setPage(next); setPageSize(size); }} />
    </div>
  </Drawer>;
}
