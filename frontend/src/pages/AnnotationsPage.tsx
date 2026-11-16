import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Breadcrumb, Button, Card, Col, Empty, Pagination, Row, Select, Space, Statistic, Table, Tabs, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import { annotationReviewStatus, getAnnotations } from '../api/annotations';
import type { Annotation } from '../api/annotations';
import { getProject, getProjects,getAnnotationStats } from '../api/projects';
import type { Project } from '../api/projects';
import { getAsset } from '../api/assets';
import type { Asset } from '../api/assets';
import AssetImage from '../components/AssetImage';
import ContinueAnnotation from '../components/ContinueAnnotation';
import ProjectWorkflow from '../components/ProjectWorkflow';
import ProjectAnnotationTools from '../components/ProjectAnnotationTools';
import ProjectAnnotationScope from '../components/ProjectAnnotationScope';
import ProjectProcessingWorkflow from '../components/ProjectProcessingWorkflow';
import { ProjectAttributeConfigDialog } from '../components/ObjectAttributes';
import MatchReviews from '../components/MatchReviews';
import { useCurrentUser } from '../components/currentUser';
const AutomationPage=lazy(()=>import('./AutomationPage'));

type ReviewRow = Annotation & { asset: Asset };
const statuses = [
  { label: '待审核', value: 'pending' }, { label: '已通过', value: 'approved' }, { label: '已驳回', value: 'rejected' },
];
const annotationContent=(data:ReviewRow['annotation_data'],taskType?:Project['task_type'])=>[
  taskType==='detection'?(data.boxes?.length?`${data.boxes.length} 个目标`:'检测负样本'):undefined,
  taskType==='classification'?data.labels?.join('、'):undefined,
  ...(taskType==='detection'||taskType==='classification'?[]:[data.description,data.attribute_schema?.fields?.map((field:{name:string})=>field.name).join('、')]),
  data.skipped?'描述与属性已跳过':undefined,
].filter(Boolean).join(' · ')||'—';

export default function AnnotationsPage() {
  const admin=useCurrentUser()?.role==='admin';
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project_id') || undefined;
  const preparing = !!projectId && admin && params.get('prepare')==='1';
  const processingTab=params.get('tool')==='history'||params.get('tool')==='single'||params.get('operation')?'history':'workflow';
  const historySource=params.get('history_source')||'workflow';
  const [editingAttributes,setEditingAttributes]=useState(false);
  const [attributeCropLabels,setAttributeCropLabels]=useState<string[]>([]);
  const [attributeRevision,setAttributeRevision]=useState(0);
  const configureAttributes=(labels:string[]=[])=>{setAttributeCropLabels(labels);setEditingAttributes(true);};
  const closeAttributes=()=>{
    setEditingAttributes(false);
    if(params.has('configure'))setParams(previous=>{
      const next=new URLSearchParams(previous);next.delete('configure');return next;
    },{replace:true,preventScrollReset:true});
  };
  const showProcessing=(tool:string,sourceJobId?:string)=>{
    const next=new URLSearchParams(params);
    next.delete('operation');next.delete('configure');
    if(tool==='workflow')next.delete('tool');else next.set('tool',tool);
    if(tool!=='workflow')next.delete('source_job_id');
    else if(sourceJobId)next.set('source_job_id',sourceJobId);
    setParams(next);
  };
  const reviewing = !preparing;
  const aiPending=params.get('ai_pending')==='1';
  const workJobId=params.get('work_job_id')||undefined;
  const imageKind=params.get('image_kind')==='originals'?'originals':params.get('image_kind')==='crops'?'crops':undefined;
  const annotationSearch = new URLSearchParams(params);
  annotationSearch.set('stage','annotate');
  const status = statuses.some(item => item.value === params.get('review_status')) ? params.get('review_status')! : undefined;
  const page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  const requestedSize = Number(params.get('page_size'));
  const pageSize = projectId ? ([20, 50, 100].includes(requestedSize) ? requestedSize : 20) : 12;
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project>();
  const kind=project?.task_type==='detection'?'detection':project?'image':undefined;
  const [stats,setStats]=useState<Pick<Project,'total_assets'|'annotated_count'|'reviewed_count'>>();
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [total, setTotal] = useState(0), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setRows([]); setProject(previous=>previous?.id===projectId?previous:undefined); setProjects([]); setTotal(0);
    (async () => {
      if (!projectId) {
        const result = await getProjects({ page, page_size: pageSize });
        if (active) { setProjects(result.items); setTotal(result.total); }
        return;
      }
      const [current, annotations,currentStats] = await Promise.all([
        getProject(projectId), reviewing ? getAnnotations({ project_id: projectId, scope:'annotation', latest_only: true, review_status: status, ai_pending:aiPending||undefined, work_job_id:workJobId,image_kind:imageKind,page,page_size:pageSize }) : undefined, getAnnotationStats(projectId),
      ]);
      const items = await Promise.all((annotations?.items || []).map(async annotation => {
        const asset = await getAsset(annotation.asset_id);
        if (asset.project_id !== projectId) throw Error('图片不属于当前项目');
        return { ...annotation, asset };
      }));
      if (active) { setProject(current); setStats(currentStats); setRows(items); setTotal(annotations?.total || 0); }
    })().catch(() => { if (active) setError('读取失败，请重试或返回标注项目重新选择'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, reviewing, status, aiPending, workJobId, imageKind, page, pageSize, reload]);

  const update = (next: { page?: number; page_size?: number; review_status?: string; kind?: string }) => {
    const search = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined) search.delete(key); else search.set(key, String(value));
    }
    setParams(search);
  };
  const columns: TableColumnsType<ReviewRow> = [
    { title: '预览', width: 88, render: (_, row) => <AssetImage assetId={row.asset_id} width={56} height={56} style={{ objectFit: 'cover', borderRadius: 4 }} /> },
    { title: '图片名称', width: 260, ellipsis: true, render: (_, row) => row.asset.original_name },
    { title: '标注内容', width: 240, ellipsis:true, render: (_, row) => annotationContent(row.annotation_data,project?.task_type) },
    { title: '版本', width: 80, render: (_, row) => <Tag>v{row.version}</Tag> },
    { title: '审核状态', width: 190, render: (_, row) => {
      const tag=(state:Annotation['review_status'],label='')=><Tag color={state==='approved'?'success':state==='rejected'?'error':'default'}>{label}{statuses.find(item=>item.value===state)?.label||state}</Tag>;
      return !kind&&row.review_by_kind?.detection&&row.review_by_kind.image
        ?<Space wrap size={2}>{tag(row.review_by_kind.detection,'检测 ')}{tag(row.review_by_kind.image,'描述／属性 ')}</Space>
        :tag(annotationReviewStatus(row,kind));
    }},
    { title: '标注来源', width: 100, render: (_, row) => row.annotation_source === 'manual' ? '手动' : row.annotation_source === 'auto' ? '自动' : row.annotation_source === 'import_yolo' ? 'YOLO导入' : row.annotation_source === 'reuse' ? '项目复用' : row.annotation_source || '—' },
    { title: '更新时间', width: 180, render: (_, row) => new Date(row.updated_at).toLocaleString('zh-CN') },
    { title: '操作', width: 150, fixed: 'right', render: (_, row) => {
      const search=new URLSearchParams(params);search.set('from','review');search.set('kind',kind||(row.annotation_data.boxes?'detection':'image'));
      return <Link to={`/annotate/${row.asset_id}?${search}`}>打开标注 / 审核</Link>;
    }},
  ];
  const reviewLink=(ai:boolean)=>{
    const query=new URLSearchParams(params);
    query.delete('page');query.set('review_status','pending');
    if(ai){query.set('ai_pending','1');query.set('kind','detection');}
    else query.delete('ai_pending');
    return `/annotations?${query}`;
  };
  const hasProjectImages = (project?.total_assets ?? 0) > 0;
  const missingCrops = project?.label_schema?.annotation_scope==='crops' && hasProjectImages && stats?.total_assets===0;
  const counts = (item: Project, scoped=false) => <Row gutter={[12, 12]}>
    {[[scoped?'可标注图片':'图片', item.total_assets], [item.task_type==='vector'?'待描述 / 分类':'待标注', Math.max(0, item.total_assets - item.annotated_count)],
      [item.task_type==='vector'?'已描述 / 分类':'已标注', item.annotated_count], [item.task_type==='vector'?'描述 / 分类已通过':'已通过', item.reviewed_count]].map(([title, value]) =>
      <Col span={6} key={title}><Statistic title={title} value={value} styles={{ content: { fontSize: 22 } }} /></Col>)}
  </Row>;

  if (preparing) return <>
    {project&&<ProjectWorkflow project={project} active="prepare"/>}
    <Card title="批量处理" loading={loading&&!project}>
      {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>setReload(n=>n+1)}>重试读取</Button>}/>}
      {project&&<>
        <p className="workspace-help">处理步骤独立于项目标注类型。可从原图或之前的任务结果启动工作流，再进入结果图片进行标注。</p>
        <Space wrap style={{marginBottom:16}}>
          <Link to={`/annotations?project_id=${project.id}`}>进入标注与审核 →</Link>
        </Space>
        <ProjectAnnotationScope project={project} onSaved={updated=>{
          setProject(current=>current?.id===updated.id?updated:current);
          setReload(value=>value+1);
        }}/>
        <Tabs activeKey={processingTab} onChange={key=>showProcessing(key)} items={[
          {key:'workflow',label:'处理工作流',children:<ProjectProcessingWorkflow key={project.id} project={project} attributeRevision={attributeRevision} onConfigureAttributes={configureAttributes} onNavigate={showProcessing}/>},
          {key:'history',label:'历史任务',children:processingTab==='history'&&<Space orientation="vertical" size="middle" style={{width:'100%'}}>
            <Space><label htmlFor="processing-history-source">记录来源</label><Select id="processing-history-source" value={historySource} style={{width:210}}
              options={[{value:'workflow',label:'工作流'},{value:'single',label:'历史单项处理'},{value:'automation',label:'历史预标注与审核'}]}
              onChange={value=>{const next=new URLSearchParams(params);next.set('history_source',value);setParams(next);}}/></Space>
            {historySource==='single'?<ProjectAnnotationTools key={project.id} project={project} onRefresh={()=>setReload(n=>n+1)}/>
              :historySource==='automation'?<Suspense fallback={<p>正在读取历史记录…</p>}><AutomationPage key={project.id} embeddedProject={project}/></Suspense>
              :<ProjectProcessingWorkflow key={project.id} project={project} history onConfigureAttributes={configureAttributes} onNavigate={showProcessing}/>}
          </Space>},
        ]}/>
        <ProjectAttributeConfigDialog key={project.id+JSON.stringify(project.label_schema?.object_attributes)} project={project}
          cropLabels={attributeCropLabels} onTemplateSaved={()=>setAttributeRevision(value=>value+1)}
          open={editingAttributes||params.get('configure')==='1'} onClose={closeAttributes}
          onSaved={updated=>setProject(current=>current?.id===updated.id?updated:current)}/>
      </>}
    </Card>
  </>;

  return <>
    {project&&<ProjectWorkflow project={project} active="review"/>}
    <Card title={projectId ? undefined : '标注项目'}>
    {error && <Alert type="error" showIcon title={error} action={<Button onClick={() => setReload(n => n + 1)}>重试读取</Button>} style={{ marginBottom: 16 }} />}
    {projectId ? <>
      <Breadcrumb items={[{ title: <Link to={admin?'/projects':'/annotations'}>{admin?'项目管理':'标注项目'}</Link> }, { title: project?.project_name || '读取项目中' }, { title: '标注与审核' }]} style={{ marginBottom: 16 }} />
      {project && <div style={{ marginBottom: 20 }}>{counts({...project,...stats},true)}</div>}
      {missingCrops&&<Alert type="info" showIcon style={{marginBottom:16}}
        title={`项目已有 ${project.total_assets.toLocaleString('zh-CN')} 张图片，当前仅标注裁剪图`}
        description={admin?<span>先在<Link to={`/projects/${projectId}/prepare`}>批量处理</Link>中切图，或把标注范围改为「原图与裁剪图」。</span>:'请联系管理员生成裁剪图或调整项目标注范围。'}/>}
      {reviewing&&project?.task_type==='vector'&&<MatchReviews key={project.id+String(project.label_schema?.annotation_scope)} projectId={project.id}/>}
      {project&&<>
        <div className="page-toolbar">
          <Space wrap>
            <ContinueAnnotation projectId={project.id} taskType={project.task_type} reviewSearch={annotationSearch.toString()}/>
            {project.task_type==='vector'&&<Link to={`/annotate/project/${project.id}`}>向量匹配标注</Link>}
            {status!=='pending'&&<Link to={reviewLink(false)}>查看待审核 →</Link>}
            {reviewing&&kind==='detection'&&<Link to={reviewLink(!aiPending)}>{aiPending?'返回全部待审核':'AI转交待审'}</Link>}
          </Space>
          {admin&&<Link to={`/analytics?project_id=${project.id}`}>质量与效率</Link>}
        </div>
      </>}
      {reviewing&&<>
      <div className="page-toolbar">
        <div className="page-toolbar-fields">
          <label htmlFor="project-review-status">审核状态</label>
          <Select id="project-review-status" aria-label="项目审核状态" showSearch optionFilterProp="label" allowClear disabled={aiPending}
            placeholder="全部状态" value={status} options={statuses} style={{ width: 160 }}
            onChange={review_status => update({ review_status, page: 1 })} />
        </div>
      </div>
      <Table columns={columns} dataSource={rows} rowKey="id" loading={loading} scroll={{ x: 1080 }}
        locale={{ emptyText: error ? '读取失败，请重试' : missingCrops ? '还没有可标注的裁剪图' : status ? '此项目没有符合审核状态的标注' :
          project?.task_type==='segmentation'?'暂无已保存的分割标注记录':project&&['detection','classification'].includes(project.task_type)&&!(project.label_schema?.labels as string[]|undefined)?.length?'此项目还没有已保存的标注，请先完成上方准备。':project?.task_type==='vector'?'暂无图片描述或分类标注；检索匹配标注见上方审核区':project&&!(stats?.total_assets??project.total_assets)?'当前标注范围暂无图片，请先添加图片。':<span>此项目还没有已保存的标注，请从上方开始标注。</span> }}
        pagination={{ current: page, pageSize, total, showSizeChanger: true, pageSizeOptions: [20, 50, 100],
          showTotal: count => `共 ${count} 张已标注图片`, onChange: (page, page_size) => update({ page, page_size }) }} />
      </>}
    </> : <>
      <p className="workspace-help">选择项目进行检测、分类、描述或向量匹配标注，项目间标注独立。{admin&&<Link to="/image-semantics/history">查看旧图片库生成批次</Link>}</p>
      <Card loading={loading} styles={{ body: { padding: 0 } }} variant="borderless">
        {!loading && !projects.length && <Empty description={error ? '项目读取失败，请重试' : admin ? '暂无标注项目' : '暂无分配给你的项目，请联系管理员分配项目'}
          image={Empty.PRESENTED_IMAGE_SIMPLE}>{!error && admin&&<Link to="/projects">前往项目管理创建项目</Link>}</Empty>}
        <Row gutter={[16, 16]}>
          {projects.map(item => <Col xs={24} md={12} xl={8} key={item.id}>
            <section aria-label={`标注项目 ${item.project_name}`}>
              <Card title={item.project_name}>
                <p className="review-project-description" title={item.description || ''}>{item.description || '未填写项目描述'}</p>
                {counts(item)}
                <Space wrap style={{ marginTop: 20 }}>
                  {item.task_type==='segmentation'?<Tag>图像分割标注暂不支持</Tag>:<ContinueAnnotation projectId={item.id} taskType={item.task_type} reviewSearch={`project_id=${item.id}`}/>}
                  {admin&&<Link to={`/projects/${item.id}/prepare`}>切图 / 标注准备</Link>}
                  <Link to={`/annotations?project_id=${item.id}`}>标注与审核</Link>
                  {admin&&<Link to={`/projects/${item.id}/images`}>项目图片</Link>}
                </Space>
              </Card>
            </section>
          </Col>)}
        </Row>
      </Card>
      <Pagination current={page} pageSize={pageSize} total={total} showSizeChanger={false}
        showTotal={count => `共 ${count} 个项目`} onChange={page => update({ page })}
        style={{ marginTop: 20, justifyContent: 'flex-end' }} />
    </>}
  </Card></>;
}

