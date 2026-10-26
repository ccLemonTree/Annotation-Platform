import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Alert, AutoComplete, Breadcrumb, Button, Card, Checkbox, Input, message, Modal, Select, Space, Table, Tag } from 'antd';
import { DeleteOutlined, UploadOutlined } from '@ant-design/icons';
import type { TableColumnsType } from 'antd';
import { getAssets, deleteAsset,getAllAssetIds } from '../api/assets';
import type { Asset } from '../api/assets';
import { getImages, associateImages, deleteImage, getImageCollections, assignImageCollection, classifyImages, createImageCollection } from '../api/images';
import type { LibraryImage, ImageFilters, ImageCollection } from '../api/images';
import ImageUploadDialog from '../components/ImageUploadDialog';
import ImageIngestSettings from '../components/ImageIngestSettings';
import ProjectImagePicker from '../components/ProjectImagePicker';
import ImageFiltersBar from '../components/ImageFilters';
import ImageSelection from '../components/ImageSelection';
import { getProject, getProjects } from '../api/projects';
import type { Project } from '../api/projects';
import AssetImage from '../components/AssetImage';
import PagedAssetPreview from '../components/PagedAssetPreview';
import ProjectAnnotationScope from '../components/ProjectAnnotationScope';
import ProjectWorkflow from '../components/ProjectWorkflow';
import DeleteCropButton from '../components/DeleteCropButton';
import DeleteSelectedImages from '../components/DeleteSelectedImages';
import DeleteImageCollection from '../components/DeleteImageCollection';

type Row = Asset | LibraryImage;
export default function AssetsPage() {
  const [notice, context] = message.useMessage();
  const { projectId } = useParams();
  const [project,setProject] = useState<Project>();
  const [pickerOpen,setPickerOpen] = useState(false);
  const [addedProject,setAddedProject] = useState<Project>();
  const [newOpen,setNewOpen]=useState(false),[newName,setNewName]=useState('');
  const [collections,setCollections] = useState<ImageCollection[]>([]);
  const [uploadName,setUploadName] = useState(()=>{const now=new Date();return String(now.getMonth()+1).padStart(2,'0')+String(now.getDate()).padStart(2,'0');});
  const [groupOpen,setGroupOpen] = useState(false), [groupName,setGroupName] = useState('');
  const [projects,setProjects] = useState<Project[]>([]);
  const [rows,setRows] = useState<Row[]>([]);
  const [filters,setFilters] = useState<ImageFilters>({});
  const [nameQuery,setNameQuery]=useState('');
  const [page,setPage] = useState(1), [pageSize,setPageSize] = useState(20), [total,setTotal] = useState(0);
  const [selected,setSelected] = useState<React.Key[]>([]);
  const [loading,setLoading] = useState(false), [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [uploadOpen,setUploadOpen] = useState(false), [associateOpen,setAssociateOpen] = useState(false);
  const [target,setTarget] = useState<string>();
  const [includeImported,setIncludeImported]=useState(false);
  const [reload,setReload] = useState(0);
  const [imageKind,setImageKind]=useState<'all'|'originals'|'crops'>('all');
  const previewScope=JSON.stringify([projectId,filters,page,pageSize,imageKind]);
  const [preview,setPreview] = useState<{index:number;scope:string}>();
  if(preview&&preview.scope!==previewScope)setPreview(undefined);
  useEffect(()=>{
    if(projectId||!rows.some(row=>'scene_status' in row&&['pending','processing'].includes(row.scene_status)))return;
    const timer=setTimeout(()=>setReload(n=>n+1),3000);
    return ()=>clearTimeout(timer);
  },[projectId,rows]);
  useEffect(()=>{
    if(projectId)return;
    let active=true;
    getImageCollections().then(items=>{if(active)setCollections(items);})
      .catch(()=>{if(active)setError('图片集列表读取失败，请刷新重试');});
    return ()=>{active=false;};
  },[reload,projectId,filters,page,pageSize]);
  useEffect(() => {
    let active=true;
    setSelected([]);
    if(projectId){
      setProject(undefined);setPickerOpen(false);
      getProject(projectId).then(value=>{if(active)setProject(value);})
        .catch(()=>{if(active)setError('项目读取失败，请返回项目管理重试');});
      return ()=>{active=false;};
    }
    (async()=>{
      const items:Project[]=[];
      for(let page=1;;page++){
        const result=await getProjects({page,page_size:100});items.push(...result.items);
        if(items.length>=result.total||!result.items.length)break;
      }
      if(active)setProjects(items);
    })().catch(()=>{if(active)setError('项目列表读取失败，请刷新重试');});
    return ()=>{active=false;};
  },[projectId]);
  useEffect(()=>{
    let active=true;setLoading(true);setError('');
    const request=projectId ? getAssets({project_id:projectId,page,page_size:pageSize,image_kind:imageKind,q:filters.q}) : getImages({...filters,page,page_size:pageSize});
    request.then(result=>{if(active){setRows(result.items);setTotal(result.total);}})
      .catch(()=>{if(active)setError('图片列表读取失败，请重试');})
      .finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[projectId,filters,page,pageSize,reload,imageKind]);
  const filter=(next:ImageFilters)=>{setFilters(next);setPage(1);setSelected([]);};
  const batchCompleted=(ids:string[])=>{
    const done=new Set(ids);setSelected(keys=>keys.filter(key=>!done.has(String(key))));setReload(n=>n+1);
  };
  const currentCollection=collections.find(group=>group.id===filters.collection_id);
  const openUpload=()=>{
    const previous=collections.find(group=>group.name===localStorage.getItem('image-upload-collection'));
    setError('');setUploadName(currentCollection?.name||previous?.name||collections[0]?.name||'');setUploadOpen(true);
  };
  const ingest=currentCollection?.ingest_config;
  const createCollection=async()=>{
    if(!newName.trim()){setError('请输入图片集名称');return;}
    setBusy(true);setError('');
    try{const group=await createImageCollection(newName.trim());setNewOpen(false);setSelected([]);filter({collection_id:group.id});setReload(n=>n+1);notice.success('图片集已创建，请点击图片集设置配置规则');}
    catch{setError('创建图片集失败，请重试');}finally{setBusy(false);}
  };
  const associate=async()=>{
    if(!target){setError('请选择标注项目');return;}
    setBusy(true);setError('');
    try{
      const result=await associateImages(target,selected.map(String),batchCompleted,includeImported);
      notice.success(`已添加 ${result.added_count} 张图片`+(includeImported?`，带入标注 ${result.annotated}，冲突保留 ${result.conflicts}`:'；项目中已有的图片不会重复添加'));
      setAddedProject(projects.find(project=>project.id===target));
      setAssociateOpen(false);setSelected([]);setReload(n=>n+1);
    }catch{setError('添加未全部完成；成功批次已生效，剩余选择已保留，请重试');}finally{setBusy(false);}
  };
  const group=async()=>{
    if(!groupName.trim()){setError('请输入图片集名称');return;}
    setBusy(true);setError('');
    try{
      const result=await assignImageCollection(groupName.trim(),selected.map(String),batchCompleted);
      notice.success(`已将 ${result.added_count} 张图片加入图片集，同图不重复存储`);
      setGroupOpen(false);setSelected([]);setFilters({...filters,collection_id:result.collection_id});setPage(1);setReload(n=>n+1);
    }catch{setError('归集未全部完成；成功批次已生效，剩余选择已保留，请重试');}finally{setBusy(false);}
  };
  const classify=async()=>{
    setBusy(true);setError('');
    if(!filters.collection_id){setError('请先选择图片集');setBusy(false);return;}
    try{const result=await classifyImages(selected.map(String),filters.collection_id,batchCompleted);notice.success(`已排队分类 ${result.queued_count} 张图片`);setSelected([]);setReload(n=>n+1);}
    catch{setError('分类未全部排队；成功批次已生效，剩余选择已保留，请检查图片集分类设置后重试');}
    finally{setBusy(false);}
  };
  const remove=(row:Row)=>{
    Modal.confirm({
      title:projectId?'从项目移除此图片？':'删除图片库记录？',
      content:projectId?'此项目中的标注与审核记录会删除，图片库和其他项目保持不变。':'被项目使用的图片不能删除。原图文件与冻结包继续保留。',
      okText:'确认',cancelText:'取消',
      onOk:async()=>{
        try{if(projectId)await deleteAsset(row.id);else await deleteImage(row.id);setReload(n=>n+1);setSelected(keys=>keys.filter(key=>key!==row.id));}
        catch{setError('删除失败：请先移除项目引用，或检查网络');}
      },
    });
  };
  const columns:TableColumnsType<Row>=[
    {title:'预览',width:90,render:(_,row,index)=><AssetImage {...(projectId?{assetId:row.id}:{imageId:row.id})} preview={false} onClick={()=>setPreview({index,scope:previewScope})}
      role="button" tabIndex={0} aria-label={`预览 ${row.original_name}`} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setPreview({index,scope:previewScope});}}}
      alt={row.original_name} width={60} height={60} style={{objectFit:'cover',borderRadius:4,cursor:'pointer'}}/>},
    {title:'文件名',dataIndex:'original_name',width:230,ellipsis:true},
    ...(!projectId?[{title:'图片集',width:160,render:(_:unknown,row:Row)=><Space wrap>{('collection_names' in row ? row.collection_names||[] : []).map(name=><Tag key={name}>{name}</Tag>)}</Space>}]:[]),
    ...(!projectId?[
      {title:'场景',width:180,render:(_:unknown,row:Row)=><Space wrap>{'scene_labels' in row&&row.scene_labels?.map(label=><Tag key={label}>{label}</Tag>)}</Space>},
      {title:'分类状态',width:130,render:(_:unknown,row:Row)=>'scene_status' in row?<Tag title={row.scene_error||undefined} color={row.scene_status==='failed'?'error':row.scene_status==='completed'?'success':'default'}>{({unclassified:'未分类',pending:'待分类',processing:'分类中',completed:'已分类',failed:'分类失败'} as Record<string,string>)[row.scene_status]}</Tag>:null},
      {title:'导入标注',width:120,render:(_:unknown,row:Row)=>'has_imported_annotation' in row&&row.has_imported_annotation?<Tag color={row.imported_annotation_trusted?'success':'default'}>{row.imported_annotation_trusted?'YOLO · 已信任':'YOLO · 待审核'}</Tag>:'—'},
    ]:[]),
    {title:'来源',dataIndex:'source_type',width:100,render:value=><Tag>{value==='model_crop'?'项目裁剪图':value==='upload'?'上传':value==='smarteye'?'SmartEye':value}</Tag>},
    ...(projectId?[{title:'标注状态',width:110,render:(_:unknown,row:Row)=><Tag>{'annotation_status' in row ? ({pending:'待标注',annotating:'标注中',completed:'待审核',reviewed:'已通过'}[row.annotation_status]):'—'}</Tag>}]:[
      {title:'首次入库批次',dataIndex:'batch_id',width:160,ellipsis:true,render:(value:string|null)=>value||'历史图片'},
    ]),
    {title:'尺寸',width:110,render:(_,row)=>row.width&&row.height?`${row.width} × ${row.height}`:'—'},
    {title:'大小',dataIndex:'file_size',width:110,render:(size:number)=>`${(size/1024).toFixed(1)} KB`},
    {title:projectId?'加入项目时间':'入库时间',dataIndex:'created_at',width:180,render:(value:string)=>new Date(value).toLocaleString()},
    {title:'操作',width:projectId?240:160,fixed:'right',render:(_,row)=><Space wrap>
      {projectId&&(project?.label_schema?.annotation_scope!=='crops'||row.source_type==='model_crop'?<Link to={`/annotate/${row.id}`}>标注 / 审核</Link>:<span>切图来源</span>)}
      {projectId&&row.source_type==='model_crop'?<DeleteCropButton assetId={row.id} name={row.original_name} disabled={busy} onDeleted={()=>{setSelected(keys=>keys.filter(key=>key!==row.id));if(rows.length===1&&page>1)setPage(page-1);setReload(n=>n+1);}}/>:<Button type="link" danger icon={<DeleteOutlined/>} onClick={()=>remove(row)}>{projectId?'移除':'删除'}</Button>}
    </Space>},
  ];
  return <Card title={projectId?`${project?.project_name || '读取项目中'} · 项目图片`:'独立图片库'}>
    {project&&projectId&&<ProjectWorkflow project={project} active="images"/>}
    {context}
    {preview&&preview.scope===previewScope&&rows[preview.index]&&<PagedAssetPreview rows={rows} index={preview.index} page={page} pageSize={pageSize} total={total}
      projectId={projectId} imageKind={imageKind} filters={filters} onClose={()=>setPreview(undefined)}/>}
    {projectId?<><Breadcrumb items={[{title:<Link to="/projects">项目管理</Link>},{title:project?.project_name||'读取项目中'},{title:'项目图片'}]} style={{marginBottom:12}}/>
      <p className="workspace-help">管理当前项目已使用的图片；从图片库添加后，在这里继续标注、审核和导出。</p></>
      :<p className="workspace-help">这里管理平台的全部原图。上传、分类和筛选后，可以选择图片添加到标注项目。</p>}
    {!projectId&&addedProject&&<Alert type="success" showIcon title={`已添加到 ${addedProject.project_name}`}
      description="可以继续整理图片，或查看该项目的图片。" style={{marginBottom:12}}
      action={<Space wrap><Link to={`/projects/${addedProject.id}/images`}>查看项目图片</Link><Link to={`/projects/${addedProject.id}/prepare`}>检测切图 / 标注准备 →</Link></Space>}/>}

    {error&&<Alert type="error" showIcon title={error} style={{marginBottom:12}}/>}
    <div className="page-toolbar">
      <div className="page-toolbar-actions">
        {projectId&&<Button type="primary" disabled={!project} onClick={()=>setPickerOpen(true)}>从图片库添加</Button>}
        {!projectId&&<><Button onClick={()=>{setNewName(uploadName);setError('');setNewOpen(true);}}>新建图片集</Button><ImageIngestSettings collectionId={filters.collection_id} collectionName={currentCollection?.name} onSaved={()=>setReload(n=>n+1)}/></>}
        {!projectId&&<DeleteImageCollection collection={currentCollection} disabled={busy} onDeleted={(group,result)=>{
          setCollections(items=>items.filter(item=>item.id!==group.id));
          setFilters(previous=>previous.collection_id===group.id?{...previous,collection_id:undefined}:previous);
          setPage(1);setSelected([]);setReload(n=>n+1);
          if(localStorage.getItem('image-upload-collection')===group.name)localStorage.removeItem('image-upload-collection');
          notice.success(`已删除图片集及 ${result.deleted_images} 张独占图片，保留 ${result.kept_images} 张共享图片`);
        }}/>} 
        {!projectId&&<Button loading={busy} disabled={!filters.collection_id||!selected.length||busy||!ingest?.classification_enabled} onClick={classify}>场景分类 / 重试（{selected.length}）</Button>}
        {!projectId&&<Button disabled={!selected.length||busy} onClick={()=>{setError('');setGroupName(uploadName);setGroupOpen(true);}}>加入图片集（{selected.length}）</Button>}
        {!projectId&&<Button disabled={!selected.length||busy} onClick={()=>{setError('');setIncludeImported(false);setAssociateOpen(true);}}>添加到标注项目（{selected.length}）</Button>}
        {!projectId&&<Button type="primary" icon={<UploadOutlined/>} onClick={openUpload}>上传图片</Button>}
      </div>
    </div>
    {projectId&&project&&<><ProjectAnnotationScope project={project} onSaved={setProject}/><Space wrap style={{marginBottom:16}}><Select aria-label="项目图片来源" style={{width:180}} value={imageKind} onChange={value=>{setImageKind(value);setPage(1);setSelected([]);}} options={[{value:'all',label:'全部项目图片'},{value:'originals',label:'仅原图'},{value:'crops',label:'仅裁剪图'}]}/>
      <Input.Search aria-label="项目图片文件名" placeholder="按图片文件名搜索" allowClear maxLength={255} style={{width:320,maxWidth:'100%'}} value={nameQuery} disabled={busy}
        onChange={event=>{setNameQuery(event.target.value);if(!event.target.value)filter({...filters,q:undefined});}}
        onSearch={value=>filter({...filters,q:value.trim()||undefined})} enterButton="搜索"/>
      <DeleteSelectedImages ids={selected.map(String)} disabled={busy||loading} onDeleted={()=>{setSelected([]);setPage(1);setReload(n=>n+1);}}/><DeleteSelectedImages disabled={busy||loading||!total} allLabel={filters.q?'删除搜索结果图片':imageKind==='crops'?'删除全部裁剪图':'删除全部项目图片'} rangeLabel={`${project.project_name} · ${{all:'全部项目图片',originals:'全部原图关联',crops:'全部裁剪图'}[imageKind]}${filters.q?` · 文件名包含“${filters.q}”`:''}`} loadAll={()=>getAllAssetIds({project_id:projectId,image_kind:imageKind,q:filters.q})} onDeleted={()=>{setSelected([]);setPage(1);setReload(n=>n+1);}}/></Space></>}
    {!projectId&&<>
      <ImageFiltersBar value={filters} collections={collections} scenes={rows.flatMap(row=>'scene_labels' in row?row.scene_labels:[])}
        disabled={busy} onChange={filter}/>
      <ImageSelection filters={filters} count={selected.length} busy={busy} disabled={loading}
        onBusy={setBusy} onSelect={setSelected} onError={setError}/>
    </>}
    {!projectId&&<p className="workspace-help">{currentCollection?`当前图片集：${currentCollection.name}；配置仅作用于此集。`:'请先选择图片集，再修改入库规则或发起分类。'} 同集按内容去重，场景随原图共享；重新分类会更新该图在所有图片集中的场景。</p>}
    <Table columns={columns} dataSource={rows} rowKey="id" loading={loading} scroll={{x:projectId?1260:1570}}
      rowSelection={{selectedRowKeys:selected,onChange:setSelected,preserveSelectedRowKeys:true,getCheckboxProps:()=>({disabled:busy||loading})}}
      locale={{emptyText:projectId?<span>当前筛选下暂无图片，可清除筛选、从图片库添加，或<Link to={`/projects/${projectId}/prepare`}>检测切图</Link>。</span>:<span>暂无匹配图片，<Button type="link" onClick={openUpload}>上传图片</Button></span>}}
      pagination={{current:page,pageSize,total,placement:['topEnd','bottomEnd'],showSizeChanger:true,pageSizeOptions:[20,50,100],disabled:busy,showTotal:n=>`共 ${n} 张`,onChange:(p,size)=>{setPage(p);setPageSize(size);}}}/>
    {pickerOpen&&project&&<ProjectImagePicker projectId={project.id} projectName={project.project_name} taskType={project.task_type}
      onClose={()=>setPickerOpen(false)} onAdded={(count,summary)=>{
        setPickerOpen(false);setReload(n=>n+1);notice.success(summary||(count?`已添加 ${count} 张图片到 ${project.project_name}`:'所选图片已在项目中'));
      }}/>}
    <Modal title="新建图片集" open={newOpen} onCancel={()=>{if(!busy)setNewOpen(false);}} onOk={createCollection} confirmLoading={busy} okText="创建" cancelText="取消">
      <p>先创建空图片集，再独立配置分辨率和场景分类。同名集继续复用。</p>
      {error&&<Alert type="error" title={error}/>}
      <Input aria-label="新图片集名称" value={newName} onChange={event=>setNewName(event.target.value)} maxLength={200} disabled={busy}/>
    </Modal>
    <ImageUploadDialog open={uploadOpen} initialName={uploadName} collections={collections} onClose={()=>{setUploadOpen(false);setReload(n=>n+1);}}
      onViewCollection={id=>{filter({collection_id:id});setReload(n=>n+1);}}
      onImported={()=>{setSelected([]);setReload(n=>n+1);}}/>
    <Modal title="加入图片集" open={groupOpen} onCancel={()=>{if(!busy)setGroupOpen(false);}} onOk={group}
      confirmLoading={busy} okText="加入" cancelText="取消">
      <p>将所选 {selected.length} 张图片加入命名图片集，已有归属和项目标注保留。</p>
      {error&&<Alert type="error" title={error}/>}
      <AutoComplete value={groupName} onChange={setGroupName} disabled={busy} style={{width:'100%'}}
        options={collections.map(group=>({value:group.name}))} filterOption={(input,option)=>(option?.value||'').toLowerCase().includes(input.toLowerCase())}>
        <Input aria-label="归类图片集名称" maxLength={200} placeholder="输入新名称或选择已有图片集" disabled={busy}/>
      </AutoComplete>
    </Modal>
    <Modal title="添加到标注项目" open={associateOpen} onCancel={()=>{if(!busy)setAssociateOpen(false);}} onOk={associate}
      confirmLoading={busy} okText="添加图片" cancelText="取消">
      <p>已选 {selected.length} 张图片，只建立项目使用记录，不复制原图。可多次选择不同批次。</p>
      {error&&<Alert type="error" title={error}/>}
      <Select aria-label="图片添加到的项目" placeholder="选择标注项目" value={target} onChange={value=>{setTarget(value);setIncludeImported(false);}} disabled={busy} showSearch optionFilterProp="label"
        style={{width:'100%'}} options={projects.map(project=>({label:project.project_name,value:project.id}))}/>
      <Checkbox style={{marginTop:12}} checked={includeImported} disabled={busy||projects.find(project=>project.id===target)?.task_type!=='detection'} onChange={event=>setIncludeImported(event.target.checked)}>带入已有YOLO标注</Checkbox>
      <p className="workspace-help">创建项目独立副本，保留导入时的信任状态；已有项目标注不覆盖。空项目采用导入类别，已有类别须包含导入类别。</p>
    </Modal>
  </Card>;
}

