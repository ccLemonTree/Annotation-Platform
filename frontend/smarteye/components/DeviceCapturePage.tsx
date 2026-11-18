import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Checkbox,Drawer,Form,Input,InputNumber,Modal,Popconfirm,Progress,Select,Space,Table,Tag,TreeSelect,message} from 'antd';
import {getImageCollections,type ImageCollection,type SceneGroup} from '../../src/api/images';
import {configSceneGroups,sceneLabel,sceneTree,sceneResultStatus} from '../../src/components/SceneCategories';
import {type Project} from '../../src/api/projects';
import {AuthImage} from '../network';
import CreateProjectDialog from './CreateProjectDialog';
import DeviceSceneSettings from './DeviceSceneSettings';
import ImagePreview from './ImagePreview';
import {openProjectAutomation,withCreatedProjects} from '../projectActions';
import {allProjects,deviceKey,managementApi,sceneStatuses,taskKinds,taskStatuses,type CaptureSchedule,type Device,type ManagedPlatform,type ManagementTask} from '../managementApi';

export default function DeviceCapturePage(){
  const params=new URLSearchParams(location.search);
  const initialProject=params.get('project_id')||undefined;
  const vectorTest=!!initialProject&&params.get('vector_test')==='1';
  const [platforms,setPlatforms]=useState<ManagedPlatform[]>([]),[projects,setProjects]=useState<Project[]>([]),[collections,setCollections]=useState<ImageCollection[]>([]);
  const [rows,setRows]=useState<Device[]>([]),[total,setTotal]=useState(0),[selected,setSelected]=useState<Device[]>([]);
  const [filters,setFilters]=useState({platform_id:'',q:'',scene:[] as string[],camera_position:[] as string[],scene_match:'any',scene_status:'',has_image:'',page:1,page_size:20});
  const [scenes,setScenes]=useState<string[]>([]),[classificationEnabled,setClassificationEnabled]=useState(false);
  const [sceneGroups,setSceneGroups]=useState<SceneGroup[]>([]);
  const [progressOpen,setProgressOpen]=useState(false);
  const cameraScenes=sceneGroups.find(group=>group.name==='摄像机位')?.scenes||[];
  const businessGroups=sceneGroups.filter(group=>group.name!=='摄像机位');
  const businessScenes=scenes.filter(scene=>scene!=='摄像机位'&&!cameraScenes.includes(scene));
  const [schedules,setSchedules]=useState<CaptureSchedule[]>([]),[tasks,setTasks]=useState<ManagementTask[]>([]),[projectFilter,setProjectFilter]=useState<string|undefined>(initialProject);
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [selectingAll,setSelectingAll]=useState(false),[selectionProgress,setSelectionProgress]=useState({loaded:0,total:0});
  const selectionVersion=useRef(0);
  const [open,setOpen]=useState(false),[editingScene,setEditingScene]=useState<Device>(),[manualScenes,setManualScenes]=useState<string[]>([]);
  const [createError,setCreateError]=useState('');
  const [creatingProject,setCreatingProject]=useState(false);
  const [previewUrl,setPreviewUrl]=useState<string|null>(null);
  const [onlineOnly,setOnlineOnly]=useState(false),[sceneSettingsOpen,setSceneSettingsOpen]=useState(false);
  const createdProjects=useRef<Project[]>([]);
  const [form]=Form.useForm();
  const taskSnapshot=useRef('');
  const report=(error:unknown)=>setError(error instanceof Error?error.message:String(error));
  const deviceQuery=()=>new URLSearchParams(Object.entries(filters).flatMap(([key,value])=>Array.isArray(value)?value.map(item=>[key,item]):[[key,String(value)]]));
  const selectionScope=JSON.stringify([onlineOnly,Object.entries(filters).filter(([key])=>key!=='page'&&key!=='page_size')]);
  useEffect(()=>{selectionVersion.current++;setSelected([]);setSelectingAll(false);},[selectionScope]);
  useEffect(()=>()=>{selectionVersion.current++;},[]);
  const loadPlatforms=async()=>setPlatforms(await managementApi<ManagedPlatform[]>('/managed-platforms'));
  useEffect(()=>{if(onlineOnly&&filters.platform_id&&!platforms.some(platform=>platform.plat_id===filters.platform_id&&platform.state==='1'&&platform.connection_status==='online'))setFilters(previous=>({...previous,platform_id:'',page:1}));},[onlineOnly,platforms,filters.platform_id]);
  const loadDevices=async()=>{setLoading(true);try{const data=await managementApi<{items:Device[];total:number}>(`/devices?${deviceQuery()}`);setRows(data.items);setTotal(data.total);}catch(error){report(error);}finally{setLoading(false);}};
  const loadTasks=async()=>{
    const path='/device-capture/schedules'+(vectorTest?`?vector_test=1&project_id=${encodeURIComponent(initialProject)}`:'');
    const [nextSchedules,nextTasks]=await Promise.all([managementApi<CaptureSchedule[]>(path),managementApi<ManagementTask[]>('/tasks')]);
    const visible=nextTasks.filter(task=>task.kind in taskKinds&&(vectorTest?
      task.kind==='device-capture'&&task.vector_test&&task.project_id===initialProject:!task.vector_test));
    setSchedules(nextSchedules);setTasks(visible);
    return visible;
  };
  useEffect(()=>{
    void Promise.all([managementApi<ManagedPlatform[]>('/managed-platforms'),allProjects(),getImageCollections(),managementApi<{scenes:string[];scene_groups?:SceneGroup[];classification_enabled:boolean}>('/device-scenes/config')]).then(([platforms,projects,collections,config])=>{setPlatforms(platforms);setProjects(withCreatedProjects(projects,createdProjects.current));setCollections(collections);setScenes(config.scenes);setSceneGroups(configSceneGroups(config));setClassificationEnabled(config.classification_enabled);}).catch(report);
  },[]);
  useEffect(()=>{
    let active=true;
    const refresh=()=>{void allProjects().then(items=>{if(active)setProjects(withCreatedProjects(items,createdProjects.current));}).catch(error=>{if(active)report(error);});};
    window.addEventListener('smarteye:projects-updated',refresh);
    return()=>{active=false;window.removeEventListener('smarteye:projects-updated',refresh);};
  },[]);
  useEffect(()=>{void loadDevices();},[filters]);
  useEffect(()=>{
    let active=true;
    const refresh=async()=>{try{const items=await loadTasks();const snapshot=JSON.stringify(items.filter(task=>task.kind in taskKinds).map(task=>[task.task_id,task.status,task.progress,task.message]));if(active&&snapshot!==taskSnapshot.current){taskSnapshot.current=snapshot;await loadDevices();const platforms=await managementApi<ManagedPlatform[]>('/managed-platforms');if(active)setPlatforms(platforms);}}catch(error){if(active)report(error);}};
    void refresh();const timer=setInterval(()=>{void refresh();},4000);
    return()=>{active=false;clearInterval(timer);};
  },[filters]);
  const execute=async(path:string,method='POST',body?:unknown)=>{
    setBusy(true);setError('');
    try{await managementApi(path,method,body);await Promise.all([loadTasks(),loadDevices(),loadPlatforms()]);message.success('操作已提交');return true;}catch(error){report(error);return false;}finally{setBusy(false);}
  };
  const references=selected.map(({plat_id,device_id})=>({plat_id,device_id}));
  const scheduleCount=Math.ceil(references.length/1000);
  const clearSelection=()=>{selectionVersion.current++;setSelectingAll(false);setSelected([]);};
  const selectAllFiltered=async()=>{
    const version=++selectionVersion.current,query=deviceQuery(),matches=new Map<string,Device>();
    let expected:number|undefined;
    setSelectingAll(true);setSelectionProgress({loaded:0,total:0});setError('');query.set('page_size','200');
    try{
      for(let page=1;;page++){
        if(version!==selectionVersion.current)return;
        query.set('page',String(page));
        const data=await managementApi<{items:Device[];total:number}>(`/devices?${query}`);
        if(version!==selectionVersion.current)return;
        if(expected!==undefined&&data.total!==expected)throw Error('筛选结果在加载期间发生变化，未更新选择；请稍后重试');
        expected=data.total;
        for(const device of data.items){const key=deviceKey(device);if(matches.has(key))throw Error('筛选结果在加载期间发生变化，未更新选择；请稍后重试');matches.set(key,device);}
        if(matches.size>expected)throw Error('筛选结果在加载期间发生变化，未更新选择；请稍后重试');
        setSelectionProgress({loaded:matches.size,total:expected});
        if(matches.size===expected)break;
        if(!data.items.length)throw Error('设备分页提前结束，未更新选择；请稍后重试');
      }
      setSelected([...matches.values()]);message.success(`已选择全部筛选结果 ${matches.size} 台设备`);
    }catch(error){if(version===selectionVersion.current)report(error);}
    finally{if(version===selectionVersion.current)setSelectingAll(false);}
  };
  const create=async()=>{
    const values=await form.validateFields().catch(()=>undefined);
    if(!values)return;
    if(!references.length){setCreateError('请选择设备');return;}
    setBusy(true);setCreateError('');
    try{
      const destination=vectorTest?{project_id:initialProject,vector_test:true}:{};
      const result=await managementApi<CaptureSchedule|CaptureSchedule[]>('/device-capture/schedules','POST',{...values,...destination,devices:references,split_devices:scheduleCount>1});
      const items=Array.isArray(result)?result:[result];
      const createdIds=new Set(items.map(item=>item.sched_id));
      setSchedules(previous=>[...items,...previous.filter(schedule=>!createdIds.has(schedule.sched_id))]);
      setOpen(false);
      message.success(`已创建 ${items.length} 个定时拉图计划`);
    }catch(error){setCreateError(error instanceof Error?error.message:String(error));}
    finally{setBusy(false);}
  };
  const updateFilter=(key:keyof typeof filters,value:string)=>{setFilters(previous=>({...previous,[key]:value,page:1}));};
  const projectName=(id?:string)=>id?projects.find(project=>project.id===id)?.project_name||id:'仅入图片库';
  const representativeUrl=(device:Device)=>`/api/smarteye/devices/${encodeURIComponent(device.plat_id)}/${encodeURIComponent(device.device_id)}/preview`;
  const onlinePlatforms=platforms.filter(platform=>platform.state==='1'&&platform.connection_status==='online');
  const visiblePlatforms=onlineOnly?onlinePlatforms:platforms;
  const syncPlatformIds=filters.platform_id?[filters.platform_id]:onlineOnly?onlinePlatforms.map(platform=>platform.plat_id):[];
  const platformScope=filters.platform_id?platforms.find(platform=>platform.plat_id===filters.platform_id)?.platform_name||filters.platform_id:onlineOnly?'全部在线平台':'全部启用平台';
  const emptyPlatformScope=onlineOnly&&!onlinePlatforms.length;
  const activeSync=tasks.filter(task=>task.kind==='device-sync'&&['pending','running','processing'].includes(task.status));
  const activeClassification=tasks.filter(task=>task.kind==='device-scenes'&&['pending','running','processing'].includes(task.status));
  const progressPlatforms=visiblePlatforms.filter(platform=>!filters.platform_id||platform.plat_id===filters.platform_id);
  const classificationStatsReady=progressPlatforms.length>0&&progressPlatforms.every(platform=>(['device_count','classified_count','scene_unmatched_count','scene_failed_count','scene_pending_count'] as const).every(key=>typeof platform[key]==='number'));
  const classificationTotals=progressPlatforms.reduce((sum,platform)=>({total:sum.total+(platform.device_count||0),success:sum.success+(platform.classified_count||0),unmatched:sum.unmatched+(platform.scene_unmatched_count||0),failed:sum.failed+(platform.scene_failed_count||0),pending:sum.pending+(platform.scene_pending_count||0)}),{total:0,success:0,unmatched:0,failed:0,pending:0});
  const classified=classificationTotals.success+classificationTotals.unmatched+classificationTotals.failed;
  const classificationPercent=classificationTotals.total?Math.floor(classified*100/classificationTotals.total):0;
  const taskSummary=(task:ManagementTask)=>{
    if(task.kind==='device-scenes')return task.processed_count===undefined?'等待分类结果':`已处理 ${task.processed_count} 台：${task.unmatched_count===undefined?'旧记录未区分是否匹配场景':`成功 ${task.processed_count-(task.failed_count||0)-task.unmatched_count} 台，未匹配 ${task.unmatched_count} 台`}，失败 ${task.failed_count||0} 台；跳过 ${task.skipped_count||0} 台`;
    const results=task.kind==='device-sync'?task.platform_results:task.device_results;
    if(!results?.length)return ['pending','running','processing'].includes(task.status)?'等待处理结果':'未记录处理数量';
    const success=results.filter(item=>item.status==='completed').length,failed=results.filter(item=>item.status==='failed').length;
    const imageSummary=results.some(item=>item.image_count!==undefined)?`；代表图 ${results.reduce((sum,item)=>sum+(item.image_count||0),0)} 台（复用 ${results.reduce((sum,item)=>sum+(item.image_reused_count||0),0)}），无图 ${results.reduce((sum,item)=>sum+(item.image_missing_count||0),0)}，取图失败 ${results.reduce((sum,item)=>sum+(item.image_failed_count||0),0)}`:'';
    return task.kind==='device-sync'?`完成 ${success} 个平台，失败 ${failed} 个；同步 ${results.reduce((sum,item)=>sum+(item.count||0),0)} 台设备${imageSummary}`:`完成 ${success} 台设备，失败 ${failed} 台；入库 / 复用 ${results.reduce((sum,item)=>sum+(item.downloaded??item.downloaded_count??0),0)} 张图片`;
  };
  return <section style={{padding:24,minWidth:0}}>
    <h1>{vectorTest?'选择抓图设备':'设备抓图与场景'}</h1>
    <p>{vectorTest?'每轮拉取所选设备最新成功的抓图，供当前项目的向量检索测试使用。':'同步设备时自动补取最近一张成功抓图，已有本地代表图则复用；取图后按图片分类，取不到则按设备名称分类，可人工修正。设备后续图片继承场景；定时拉图每轮仅取设备最新成功的一张入库并关联项目，沿用已有场景，不更新设备代表图或自动重新分类。'}</p>
    {error&&<Alert type="error" showIcon title={error} closable onClose={()=>setError('')} style={{marginBottom:16}}/>}
    <Space wrap style={{marginBottom:16}}>
      <Checkbox checked={onlineOnly} onChange={event=>{setOnlineOnly(event.target.checked);if(event.target.checked&&!onlinePlatforms.some(platform=>platform.plat_id===filters.platform_id))updateFilter('platform_id','');}}>仅在线平台</Checkbox>
      <Select aria-label="设备平台" placeholder={onlineOnly?'全部在线平台':'全部平台'} allowClear showSearch optionFilterProp="label" style={{width:200}} value={filters.platform_id||undefined} onChange={value=>updateFilter('platform_id',value||'')} options={visiblePlatforms.map(platform=>({value:platform.plat_id,label:`${platform.platform_name}（${platform.connection_status==='online'?'在线':'未连接'}）`}))}/>
      <Input.Search aria-label="搜索设备" placeholder="设备名称或ID" onSearch={value=>updateFilter('q',value)} style={{width:220}}/>
      <TreeSelect aria-label="设备场景" placeholder="业务场景（可多选）" multiple allowClear showSearch treeDefaultExpandAll treeNodeFilterProp="label" treeNodeLabelProp="label" maxTagCount="responsive" style={{width:240}} value={filters.scene} onChange={scene=>{setSelected([]);setFilters(previous=>({...previous,scene,page:1}));}} treeData={sceneTree(businessGroups,businessScenes)}/>
      <Select aria-label="摄像机位" placeholder="全部摄像机位" mode="multiple" allowClear style={{width:220}} value={filters.camera_position}
        options={cameraScenes.map(scene=>({value:scene,label:scene}))} onChange={camera_position=>setFilters(previous=>({...previous,camera_position,page:1}))}/>
      <Select aria-label="场景匹配方式" value={filters.scene_match} style={{width:160}} onChange={value=>{setSelected([]);updateFilter('scene_match',value);}} options={[{value:'any',label:'任一场景匹配'},{value:'all',label:'全部场景匹配'}]}/>
      <Select aria-label="设备分类状态" placeholder="全部分类状态" allowClear style={{width:160}} value={filters.scene_status||undefined} onChange={value=>updateFilter('scene_status',value||'')} options={Object.entries(sceneStatuses).map(([value,label])=>({value,label}))}/>
      <Select aria-label="设备图片筛选" value={filters.has_image} style={{width:140}} onChange={value=>updateFilter('has_image',value)} options={[{value:'',label:'全部图片状态'},{value:'yes',label:'有图片'},{value:'no',label:'无图片'}]}/>
      {!vectorTest&&<Button loading={busy} disabled={emptyPlatformScope} onClick={()=>execute('/devices/sync','POST',{platform_ids:syncPlatformIds})}>{filters.platform_id?'同步当前平台设备':'同步在线平台设备'}</Button>}
      <Button loading={loading} onClick={()=>{void Promise.all([loadDevices(),loadPlatforms()]).catch(report);}}>刷新设备</Button>
    </Space>
    <Space wrap style={{marginBottom:16}}>
      <span>已选 {selected.length} 台设备</span><Button loading={selectingAll} disabled={!total||loading||busy} onClick={()=>void selectAllFiltered()}>全选筛选结果</Button><Button disabled={!selected.length&&!selectingAll} onClick={clearSelection}>清空选择</Button>
      {selectingAll&&<span>正在选择 {selectionProgress.loaded} / {selectionProgress.total} 台</span>}
      {!vectorTest&&<><Button onClick={()=>setSceneSettingsOpen(true)}>场景分类设置</Button>
      <Button disabled={!selected.length||selected.length>1000||!classificationEnabled||busy||selectingAll} onClick={()=>execute('/devices/classify','POST',{devices:references})}>分类所选设备</Button>
      <Popconfirm title={`重新分类：${platformScope}？`} description="处理此范围所有分页的设备，包含已成功设备；保留人工修正。" onConfirm={()=>execute('/devices/classify','POST',{platform_ids:syncPlatformIds})}><Button disabled={!classificationEnabled||busy||emptyPlatformScope}>全部设备分类</Button></Popconfirm>
      <Popconfirm title={`重跑失败分类：${platformScope}？`} description="仅重跑此范围所有分页中当前分类失败的设备，保留成功、未匹配及人工结果。" onConfirm={()=>execute('/devices/classify','POST',{platform_ids:syncPlatformIds,only_failed:true})}><Button disabled={!classificationEnabled||busy||emptyPlatformScope}>重跑失败分类</Button></Popconfirm>
      <Button onClick={()=>setProgressOpen(true)}>同步与分类进度{activeSync.length+activeClassification.length?`（${activeSync.length+activeClassification.length} 项进行中）`:''}</Button></>}
      <Button type="primary" disabled={!selected.length||busy||selectingAll} onClick={()=>{form.resetFields();form.setFieldsValue({project_id:projectFilter,interval_seconds:3600,enabled:true});setCreateError('');setOpen(true);}}>创建定时拉图</Button>
      {scheduleCount>1&&<span>定时拉图将自动拆成 {scheduleCount} 条计划，每条最多 1,000 台。{!vectorTest&&'所选设备分类每次仍最多 1,000 台。'}</span>}
      {!vectorTest&&!classificationEnabled&&<span>自动场景分类未启用，请先配置分类模型与候选场景。</span>}
    </Space>
    {!vectorTest&&<p>同步与批量分类的平台范围：{platformScope}。设备搜索、场景、分类状态和图片筛选仅影响列表；业务场景按所选方式匹配，与摄像机位筛选取交集。启用自动分类后，新设备自动入队；已完成结果保留；定时抓图不触发分类，重新分类请手动操作。</p>}
    <Drawer title="同步与分类进度" open={progressOpen} size={560} onClose={()=>setProgressOpen(false)}>
    <section aria-label="同步与分类进度" style={{minWidth:0,overflowWrap:'anywhere'}}>

      {!activeSync.length&&<p>当前没有进行中的设备同步任务</p>}
      {activeSync.map(task=><div key={task.task_id} style={{marginBottom:16}}>
        <strong>设备同步 · 平台处理进度</strong>
        <Progress aria-label="设备同步平台进度" percent={task.progress||0} status="active"/>
        <div>已结束平台 {task.platform_results?.filter(item=>['completed','failed'].includes(item.status)).length||0} 个（失败 {task.platform_results?.filter(item=>item.status==='failed').length||0} 个），已处理设备 {task.platform_results?.reduce((sum,item)=>sum+(item.count||0),0)||0} 台</div>
        {!task.platform_results?.length&&<small>等待登录或查询平台设备列表</small>}
        {task.platform_results?.filter(item=>['pending','running','processing'].includes(item.status)).map(item=><div key={item.plat_id} style={{marginTop:8}}>
          {item.device_total===undefined?<small>{item.platform_name||item.plat_id}：正在登录或查询设备列表</small>:<>
            <small>{item.platform_name||item.plat_id}：代表图已处理 {item.count||0} / {item.device_total} 台（含无图和取图失败）</small>
            <Progress aria-label={`${item.platform_name||item.plat_id}代表图进度`} percent={item.device_total?Math.floor((item.count||0)*100/item.device_total):0} status="active" size="small"/>
          </>}
        </div>)}
      </div>)}
      <strong>场景分类 · 当前平台范围</strong>
      {classificationStatsReady?<>
        <div>{classificationTotals.total?`已处理分类 ${classified} / ${classificationTotals.total} 台（含未匹配和失败）`:'当前范围暂无已同步设备'}</div>
        <Progress aria-label="场景分类总进度" percent={classificationPercent} status={activeClassification.length?'active':'normal'}/>
        <Space wrap size={4}><Tag color="success">分类成功 {classificationTotals.success}</Tag><Tag>未匹配 {classificationTotals.unmatched}</Tag><Tag color={classificationTotals.failed?'error':undefined}>失败 {classificationTotals.failed}</Tag><Tag>排队 / 分类中 {classificationTotals.pending}</Tag><Tag>未分类 {Math.max(0,classificationTotals.total-classified-classificationTotals.pending)}</Tag></Space>
      </>:<div>等待平台分类统计</div>}
      {activeClassification.map(task=><div key={task.task_id}><small>当前分类批次已处理 {task.processed_count||0} 台，跳过 {task.skipped_count||0} 台</small></div>)}
      <div><small>分类总进度按当前平台范围内已同步设备统计，不受设备搜索和场景筛选影响；同步新增设备时，总数会随之更新。</small></div>
    </section>
    </Drawer>
    {sceneSettingsOpen&&<DeviceSceneSettings onClose={()=>setSceneSettingsOpen(false)} onSaved={config=>{setScenes(config.scenes);setSceneGroups(configSceneGroups(config));setClassificationEnabled(config.classification_enabled);setSceneSettingsOpen(false);message.success('分类设置已保存；已有设备可点击全部设备分类重新处理');}}/>}
    <Table<Device> id="device-list" rowKey={deviceKey} loading={loading} dataSource={rows} scroll={{x:1050}} rowSelection={{selectedRowKeys:selected.map(deviceKey),preserveSelectedRowKeys:true,getCheckboxProps:()=>({disabled:selectingAll}),onChange:keys=>{const records=new Map([...selected,...rows].map(device=>[deviceKey(device),device]));setSelected(keys.map(key=>records.get(String(key))).filter((device):device is Device=>!!device));}}}
      pagination={{current:filters.page,pageSize:filters.page_size,total,showSizeChanger:true,onChange:(page,page_size)=>setFilters({...filters,page,page_size}),showTotal:total=>`共 ${total} 台设备`}} columns={[
        {title:'平台',dataIndex:'platform_name'}, {title:'设备',render:(_,row)=><div>{row.device_name}<div style={{fontSize:12}}>{row.device_id}</div></div>},
        {title:'代表图',render:(_,row)=><Space direction="vertical" size={0}>{row.representative_url?<Button type="text" aria-label={`放大查看${row.device_name}代表图`} title="点击放大代表图" style={{padding:0,height:'auto',cursor:'zoom-in'}} onClick={()=>setPreviewUrl(representativeUrl(row))}><AuthImage src={representativeUrl(row)} alt={`${row.device_name}代表图`} style={{width:96,height:64,objectFit:'contain'}}/></Button>:row.representative_status==='fetching'?'正在获取代表图':'暂无抓图'}{row.representative_error&&<small style={{color:row.representative_status==='failed'?'var(--ant-color-error)':undefined}}>{row.representative_error}</small>}</Space>},
        {title:'场景',render:(_,row)=>row.scene_labels?.length?<Space wrap>{row.scene_labels.map(scene=><Tag key={scene}>{sceneLabel(scene,sceneGroups)}</Tag>)}</Space>:<span>{sceneResultStatus(row.scene_status,row.scene_labels)==='unmatched'?'暂无匹配场景':'—'}</span>},
        {title:'分类状态',render:(_,row)=>{const status=sceneResultStatus(row.scene_status,row.scene_labels);return <Space direction="vertical" size={0}><Tag color={status==='failed'?'error':status==='completed'?'success':status==='unmatched'?'warning':'default'}>{sceneStatuses[status]||status}</Tag>{row.scene_source==='manual'?'人工修正':row.scene_source==='auto'?(row.scene_basis==='image'?'按抓图':'按设备名'):'—'}{status==='unmatched'&&<small>已处理，但未命中候选场景；可重新分类或人工修正。</small>}{row.scene_error&&<span>{row.scene_error}</span>}</Space>;}},
        ...(!vectorTest?[{title:'操作',render:(_:unknown,row:Device)=><Space wrap><Button size="small" onClick={()=>{setEditingScene(row);setManualScenes(row.scene_labels||[]);}}>修正场景</Button>{row.scene_status==='failed'&&row.scene_source!=='manual'&&<Button size="small" disabled={!classificationEnabled||busy} onClick={()=>execute('/devices/classify','POST',{devices:[{plat_id:row.plat_id,device_id:row.device_id}],only_failed:true})}>重跑分类</Button>}</Space>}]:[]),
      ]}/>
    <Space wrap style={{margin:'24px 0 12px'}}><h2>定时拉图任务</h2>{!vectorTest&&<><Select aria-label="按项目筛选拉图任务" placeholder="全部项目" allowClear showSearch optionFilterProp="label" style={{width:240}} value={projectFilter} onChange={setProjectFilter} options={projects.map(project=>({value:project.id,label:project.project_name}))}/>
      <Button onClick={()=>setCreatingProject(true)}>新建项目</Button>
      <Button disabled={projects.find(project=>project.id===projectFilter)?.task_type!=='detection'} title="请选择检测项目后进行预标注与AI审核" onClick={()=>{if(projectFilter&&projects.find(project=>project.id===projectFilter)?.task_type==='detection')openProjectAutomation(projectFilter);}}>预标注与AI审核</Button></>}
    </Space>
    {creatingProject&&<CreateProjectDialog onClose={()=>setCreatingProject(false)} onCreated={project=>{createdProjects.current=[project,...createdProjects.current];setProjects(previous=>[project,...previous.filter(item=>item.id!==project.id)]);setProjectFilter(project.id);setCreatingProject(false);window.dispatchEvent(new CustomEvent('smarteye:projects-updated',{detail:project}));}}/>}
    <Table<CaptureSchedule> rowKey="sched_id" dataSource={schedules.filter(schedule=>!projectFilter||schedule.project_id===projectFilter)} scroll={{x:1200}} columns={[
      {title:'名称',dataIndex:'name'}, {title:'设备数',render:(_,row)=>row.devices.length},
      {title:'关联项目',render:(_,row)=>projectName(row.project_id)},
      {title:'入库位置',render:(_,row)=>vectorTest?'向量检索测试':collections.find(group=>group.id===row.collection_id)?.name||row.collection_id||'按平台归类'},
      {title:'间隔',render:(_,row)=>`${row.interval_seconds??row.interval_minutes*60} 秒`},
      {title:'状态',render:(_,row)=><Space direction="vertical" size={0}><Tag color={row.running?'processing':row.enabled?'success':'default'}>{row.running?'拉取中':row.enabled?'已启用':'已暂停'}</Tag>{row.enabled&&!row.running&&row.next_run&&<small>下次：{new Date(row.next_run*1000).toLocaleString('zh-CN')}</small>}{row.last_run&&<small>上次：{new Date(row.last_run*1000).toLocaleString('zh-CN')}</small>}{row.last_error&&<span>{[...new Set(row.last_error.split('; '))].join('；')}</span>}</Space>},
      {title:'操作',render:(_,row)=><Space><Button size="small" disabled={busy||row.running} onClick={()=>execute(`/device-capture/schedules/${encodeURIComponent(row.sched_id)}/run`)}>立即拉取</Button><Button size="small" disabled={busy} onClick={()=>execute(`/device-capture/schedules/${encodeURIComponent(row.sched_id)}/${row.enabled?'stop':'start'}`)}>{row.enabled?'暂停':'启用'}</Button><Popconfirm title="删除此定时任务？" onConfirm={()=>execute(`/device-capture/schedules/${encodeURIComponent(row.sched_id)}`,'DELETE')}><Button danger size="small" disabled={busy||row.running}>删除任务</Button></Popconfirm></Space>},
    ]}/>
    {!vectorTest&&<><h2 style={{marginTop:24}}>平台设备与分类概况</h2>
    <p>已处理 = 分类成功 + 未匹配场景 + 分类失败；分类成功需有场景标签，空结果单独统计。下方执行记录保留各次任务的处理结果。</p>
    <Table<ManagedPlatform> rowKey="plat_id" dataSource={visiblePlatforms.filter(platform=>!filters.platform_id||platform.plat_id===filters.platform_id)} scroll={{x:950}} pagination={{pageSize:10,showSizeChanger:false}} columns={[
      {title:'平台',dataIndex:'platform_name'},
      {title:'连接状态',render:(_,row)=><Tag color={row.connection_status==='online'?'success':'default'}>{row.connection_status==='online'?'在线':row.connection_status==='error'?'连接异常':'未检测'}</Tag>},
      {title:'设备总数',render:(_,row)=>row.device_count??'—'},
      {title:'已处理分类',render:(_,row)=>row.classified_count===undefined?'—':row.classified_count+(row.scene_failed_count||0)+(row.scene_unmatched_count||0)},
      {title:'分类成功',render:(_,row)=><span style={{color:'var(--ant-color-success)'}}>{row.classified_count??'—'}</span>},
      {title:'分类失败',render:(_,row)=><span style={{color:row.scene_failed_count?'var(--ant-color-error)':undefined}}>{row.scene_failed_count??'—'}</span>},
      {title:'未匹配场景',render:(_,row)=><Button type="link" disabled={!row.scene_unmatched_count} onClick={()=>{setFilters(previous=>({...previous,platform_id:row.plat_id,q:'',scene:[],scene_status:'unmatched',has_image:'',page:1}));document.getElementById('device-list')?.scrollIntoView({behavior:'smooth'});}}>{row.scene_unmatched_count??0}</Button>},
      {title:'排队 / 分类中',render:(_,row)=>row.scene_pending_count??'—'},
      {title:'未分类',render:(_,row)=>row.classified_count===undefined?'—':Math.max(0,(row.device_count||0)-row.classified_count-(row.scene_failed_count||0)-(row.scene_pending_count||0)-(row.scene_unmatched_count||0))},
      {title:'操作',render:(_,row)=><Button size="small" disabled={!row.scene_failed_count} onClick={()=>{setFilters(previous=>({...previous,platform_id:row.plat_id,q:'',scene:[],scene_status:'failed',has_image:'',page:1}));document.getElementById('device-list')?.scrollIntoView({behavior:'smooth'});}}>查看失败设备</Button>},
    ]}/></>}
    <h2 style={{marginTop:24}}>执行记录（按平台查看明细）</h2>
    <p>{vectorTest?'点击“查看明细”查看本次拉图的设备和失败原因。':'先看处理摘要；点击“查看明细”查看平台、设备及失败原因。设备同步和分类作用于共享设备，项目筛选仅作用于抓图记录。'}</p>
    <Table<ManagementTask> rowKey="task_id" dataSource={tasks.filter(task=>task.kind!=='device-capture'||!projectFilter||task.project_id===projectFilter).sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''))} scroll={{x:1100}}
      expandable={{expandRowByClick:true,expandIcon:({expanded,onExpand,record})=><Button type="link" size="small" onClick={event=>{event.stopPropagation();onExpand(record,event);}}>{expanded?'收起明细':'查看明细'}</Button>,columnWidth:100,expandedRowRender:row=><div>
        <p>任务编号：{row.task_id}；最近更新：{row.updated_at?new Date(row.updated_at).toLocaleString('zh-CN'):'旧记录未记录时间'}</p>
        <p>执行说明：{row.kind==='device-capture'&&row.device_results?.length?taskSummary(row):row.message||'暂无补充说明'}</p>
        {row.platform_results?.length||row.device_results?.length?<Table size="small" pagination={{pageSize:10,showSizeChanger:false}} rowKey={(item,index)=>`${item.plat_id}:${item.device_id||''}:${index}`} dataSource={[...(row.platform_results||[]),...(row.device_results||[])].sort((a,b)=>Number(b.status==='failed')-Number(a.status==='failed'))}
          expandable={{rowExpandable:item=>!!item.device_results?.length,expandedRowRender:item=><><p>{row.kind==='device-sync'?`本次取图失败 ${item.image_failed_count||0} 台，设备按名称分类；原因也显示在设备代表图列。`:`本次分类失败 ${item.failed_count} 台；全部当前失败设备可在上方平台概况中查看。`}保留前 100 台失败设备明细。</p><Table size="small" rowKey="device_id" pagination={{pageSize:10,showSizeChanger:false}} dataSource={item.device_results} columns={[{title:'失败设备名称',render:(_,device)=>device.device_name||'名称未记录'},{title:'设备ID',dataIndex:'device_id'},{title:'失败原因',dataIndex:'error'}]}/></>}} columns={[
          {title:'平台',render:(_,item)=>item.platform_name||platforms.find(platform=>platform.plat_id===item.plat_id)?.platform_name||item.plat_id},
          {title:'设备',render:(_,item)=>item.device_id?<div>{item.device_name||'设备名称未记录'}<small style={{display:'block'}}>{item.device_id}</small></div>:row.kind==='device-sync'?'平台全部设备':'本次分类设备'},
          {title:'结果',render:(_,item)=><Tag color={item.status==='failed'?'error':item.status==='completed'?'success':'default'}>{taskStatuses[item.status]||item.status}</Tag>},
          {title:'处理数量',render:(_,item)=>row.kind==='device-sync'?`${item.count??0}${item.device_total!==undefined?` / ${item.device_total}`:''} 台设备${item.image_count!==undefined?`；代表图 ${item.image_count}（复用 ${item.image_reused_count||0}）、无图 ${item.image_missing_count||0}、取图失败 ${item.image_failed_count||0}`:''}`:row.kind==='device-scenes'?`已处理 ${item.processed_count??0} 台；${item.unmatched_count===undefined?'旧记录未区分是否匹配场景':`成功 ${item.success_count??0} 台；未匹配 ${item.unmatched_count} 台`}；失败 ${item.failed_count??0} 台；跳过 ${item.skipped_count??0} 台`:<div>入库 / 复用 {item.downloaded??item.downloaded_count??0} 张；规则过滤 {item.filtered??0} 张；无效记录 {item.skipped??0} 条</div>},
          {title:'失败原因',render:(_,item)=><span style={{whiteSpace:'normal'}}>{item.error==='平台登录失败'?'平台登录失败（旧记录未保存具体原因）':item.error||'—'}</span>},
        ]}/>:<p>此记录没有逐平台或设备明细，处理数量见摘要。</p>}
      </div>}} columns={[
      {title:'开始时间',width:180,render:(_,row)=>row.created_at?new Date(row.created_at).toLocaleString('zh-CN'):'旧记录未记录时间'},
      {title:'执行内容',render:(_,row)=><div>{taskKinds[row.kind]}{row.name&&<small style={{display:'block'}}>{row.name}</small>}</div>},
      {title:'状态',render:(_,row)=><Tag color={row.status==='failed'?'error':row.status==='completed'?'success':['running','processing'].includes(row.status)?'processing':'default'}>{taskStatuses[row.status]||row.status}</Tag>},
      {title:'作用范围',render:(_,row)=>row.kind==='device-capture'?projectName(row.project_id):'共享设备（跨项目）'},
      {title:'处理摘要',width:360,render:(_,row)=><div style={{whiteSpace:'normal'}}>{taskSummary(row)}{row.progress!==undefined&&['device-sync','device-scenes'].includes(row.kind)&&<Progress aria-label="执行记录进度" percent={row.progress} size="small" status={row.status==='failed'?'exception':['pending','running','processing'].includes(row.status)?'active':'normal'}/>} {['pending','running','processing'].includes(row.status)&&<small style={{display:'block'}}>{row.kind==='device-scenes'?'分类进行中':`进度 ${row.progress||0}%`}</small>}</div>},
      {title:'操作',render:(_,row)=>['pending','running','processing'].includes(row.status)?<Button disabled={busy} size="small" onClick={event=>{event.stopPropagation();void execute(`/tasks/${encodeURIComponent(row.task_id)}/cancel`);}}>停止执行</Button>:'—'},
    ]}/>
    {previewUrl&&<ImagePreview url={previewUrl} onClose={()=>setPreviewUrl(null)}/>}
    <Modal title="创建定时拉图" open={open} okText={scheduleCount>1?`创建 ${scheduleCount} 条计划`:'创建任务'} cancelText="取消" onOk={create} onCancel={()=>{if(!busy)setOpen(false);}} confirmLoading={busy}>
      {createError&&<Alert type="error" showIcon title={createError} />}
      <p>每轮仅拉取已选 {selected.length} 台设备各自最新成功的一张抓图，不补拉历史；相同图片复用已有记录。不会主动触发摄像头拍摄。</p>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="任务名称" rules={[{required:true,whitespace:true,max:160}]}><Input maxLength={160}/></Form.Item>
        <Form.Item name="interval_seconds" label="拉取间隔（秒）" extra="每轮完成后等待此间隔再拉取，同一计划不重叠执行。" rules={[{required:true,type:'number',min:1,max:31536000}]}><InputNumber min={1} max={31536000} precision={0}/></Form.Item>
        {vectorTest?<p>入库位置为当前项目的向量检索测试区。</p>:<>
          <Form.Item name="project_id" label="关联标注项目" extra="选择项目后默认使用项目同名图片集，新增图片自动关联此项目。"><Select aria-label="关联标注项目" allowClear showSearch optionFilterProp="label" options={projects.map(project=>({value:project.id,label:project.project_name}))}/></Form.Item>
          <Form.Item name="collection_id" label="目标图片集"><Select aria-label="定时拉图图片集" allowClear showSearch optionFilterProp="label" placeholder="默认使用关联项目名" options={collections.map(group=>({value:group.id,label:group.name}))}/></Form.Item>
        </>}
        <Form.Item name="min_long_edge" label="最小图片长边（像素）" extra={vectorTest?'留空或填 0 时不限制图片尺寸。':'留空使用默认入库规则，0 不限制图片尺寸。'}><InputNumber min={0} max={32768} precision={0}/></Form.Item>
        <Form.Item name="enabled" valuePropName="checked" extra={vectorTest?'启用后立即执行首轮拉图。':'启用后立即执行首轮拉图。图片沿用设备已有场景标签，拉图不会触发场景分类。'}><Checkbox>创建后自动定时拉图</Checkbox></Form.Item>
      </Form>
    </Modal>
    <Modal title="人工修正设备场景" width={960} open={!!editingScene} okText="保存场景" cancelText="取消" confirmLoading={busy} onCancel={()=>setEditingScene(undefined)} onOk={async()=>{if(editingScene&&await execute(`/devices/${encodeURIComponent(editingScene.plat_id)}/${encodeURIComponent(editingScene.device_id)}/scene`,'PUT',{scene_labels:manualScenes}))setEditingScene(undefined);}}>
      <p>{editingScene?.platform_name} / {editingScene?.device_name}。人工结果优先保留，批量自动分类不会覆盖人工修正。</p>
      {editingScene?.representative_url&&<AuthImage src={editingScene.representative_url} alt={`${editingScene.device_name} 代表图`} style={{display:'block',width:'100%',maxHeight:'60vh',objectFit:'contain',marginBottom:16}} />}
      <TreeSelect aria-label="人工设备场景" multiple treeDefaultExpandAll treeNodeLabelProp="label" treeNodeFilterProp="label" showSearch style={{width:'100%'}} value={manualScenes} onChange={setManualScenes} treeData={sceneTree(sceneGroups,[...scenes,...manualScenes],false)}/>
    </Modal>
  </section>;
}
