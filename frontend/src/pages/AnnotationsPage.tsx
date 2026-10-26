import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Breadcrumb, Button, Card, Col, Empty, Pagination, Row, Select, Space, Statistic, Table, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import { getAnnotations } from '../api/annotations';
import type { Annotation } from '../api/annotations';
import { getProject, getProjects,getAnnotationStats } from '../api/projects';
import type { Project } from '../api/projects';
import { getAsset } from '../api/assets';
import type { Asset } from '../api/assets';
import AssetImage from '../components/AssetImage';
import ContinueAnnotation from '../components/ContinueAnnotation';
import ProjectWorkflow from '../components/ProjectWorkflow';
import MatchReviews from '../components/MatchReviews';
import { useCurrentUser } from '../components/currentUser';

type ReviewRow = Annotation & { asset: Asset };
const statuses = [
  { label: '待审核', value: 'pending' }, { label: '已通过', value: 'approved' }, { label: '已驳回', value: 'rejected' },
];

export default function AnnotationsPage() {
  const admin=useCurrentUser()?.role==='admin';
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project_id') || undefined;
  const status = statuses.some(item => item.value === params.get('review_status')) ? params.get('review_status')! : undefined;
  const page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  const requestedSize = Number(params.get('page_size'));
  const pageSize = projectId ? ([20, 50, 100].includes(requestedSize) ? requestedSize : 20) : 12;
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project>();
  const [stats,setStats]=useState<Pick<Project,'total_assets'|'annotated_count'|'reviewed_count'>>();
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [total, setTotal] = useState(0), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setRows([]); setProject(undefined); setProjects([]); setTotal(0);
    (async () => {
      if (!projectId) {
        const result = await getProjects({ page, page_size: pageSize });
        if (active) { setProjects(result.items); setTotal(result.total); }
        return;
      }
      const [current, annotations,currentStats] = await Promise.all([
        getProject(projectId), getAnnotations({ project_id: projectId, scope:'annotation', latest_only: true, review_status: status, page, page_size: pageSize }),getAnnotationStats(projectId),
      ]);
      const items = await Promise.all(annotations.items.map(async annotation => {
        const asset = await getAsset(annotation.asset_id);
        if (asset.project_id !== projectId) throw Error('图片不属于当前项目');
        return { ...annotation, asset };
      }));
      if (active) { setProject(current); setStats(currentStats); setRows(items); setTotal(annotations.total); }
    })().catch(() => { if (active) setError('读取失败，请重试或返回标注项目重新选择'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, status, page, pageSize, reload]);

  const update = (next: { page?: number; page_size?: number; review_status?: string }) => {
    const search = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined) search.delete(key); else search.set(key, String(value));
    }
    setParams(search);
  };
  const columns: TableColumnsType<ReviewRow> = [
    { title: '预览', width: 88, render: (_, row) => <AssetImage assetId={row.asset_id} width={56} height={56} style={{ objectFit: 'cover', borderRadius: 4 }} /> },
    { title: '图片名称', width: 260, ellipsis: true, render: (_, row) => row.asset.original_name },
    { title: project?.task_type==='detection'?'目标数':'标注内容', width: 180, ellipsis:true, render: (_, row) =>
      row.annotation_data.boxes ? (row.annotation_data.boxes.length||'负样本') : row.annotation_data.skipped?'无法判断 / 跳过':
        [row.annotation_data.labels?.join('、'),row.annotation_data.description].filter(Boolean).join(' · ')||'—' },
    { title: '版本', width: 80, render: (_, row) => <Tag>v{row.version}</Tag> },
    { title: '审核状态', width: 110, render: (_, row) => <Tag color={row.review_status === 'approved' ? 'success' : row.review_status === 'rejected' ? 'error' : 'default'}>
      {statuses.find(item => item.value === row.review_status)?.label || row.review_status}</Tag> },
    { title: '标注来源', width: 100, render: (_, row) => row.annotation_source === 'manual' ? '手动' : row.annotation_source === 'auto' ? '自动' : row.annotation_source === 'import_yolo' ? 'YOLO导入' : row.annotation_source || '—' },
    { title: '更新时间', width: 180, render: (_, row) => new Date(row.updated_at).toLocaleString('zh-CN') },
    { title: '操作', width: 150, fixed: 'right', render: (_, row) =>
      <Link to={`/annotate/${row.asset_id}?from=review&${params.toString()}`}>打开标注 / 审核</Link> },
  ];
  const counts = (item: Project) => <Row gutter={[12, 12]}>
    {[['图片', item.total_assets], [item.task_type==='vector'?'待描述 / 分类':'待标注', Math.max(0, item.total_assets - item.annotated_count)],
      [item.task_type==='vector'?'已描述 / 分类':'已标注', item.annotated_count], [item.task_type==='vector'?'描述 / 分类已通过':'已通过', item.reviewed_count]].map(([title, value]) =>
      <Col span={6} key={title}><Statistic title={title} value={value} styles={{ content: { fontSize: 22 } }} /></Col>)}
  </Row>;

  return <Card title={projectId ? `${project?.project_name || '读取项目中'} · 标注与审核` : '标注项目'}>
    {error && <Alert type="error" showIcon title={error} action={<Button onClick={() => setReload(n => n + 1)}>重试读取</Button>} style={{ marginBottom: 16 }} />}
    {projectId ? <>
      <Breadcrumb items={[{ title: <Link to="/annotations">标注项目</Link> }, { title: project?.project_name || '读取项目中' }, { title: '标注与审核' }]} style={{ marginBottom: 16 }} />
      {project && <div style={{ marginBottom: 20 }}>{counts({...project,...stats})}</div>}
      {project&&<ProjectWorkflow project={project} active="review" reviewSearch={params.toString()}/>}
      {project?.task_type==='vector'&&<MatchReviews key={project.id+String(project.label_schema?.annotation_scope)} projectId={project.id}/>}
      <p className="workspace-help">{project?.task_type==='vector'?'下方显示图片描述与分类标注；检索匹配判定见上方审核区。':'下方显示当前项目的最新标注。未标注图片请从项目导航进入“开始标注”。'}</p>
      <div className="page-toolbar">
        <div className="page-toolbar-fields">
          <label htmlFor="project-review-status">审核状态</label>
          <Select id="project-review-status" aria-label="项目审核状态" showSearch optionFilterProp="label" allowClear
            placeholder="全部状态" value={status} options={statuses} style={{ width: 160 }}
            onChange={review_status => update({ review_status, page: 1 })} />
        </div>
      </div>
      <Table columns={columns} dataSource={rows} rowKey="id" loading={loading} scroll={{ x: 1080 }}
        locale={{ emptyText: error ? '读取失败，请重试' : status ? '此项目没有符合审核状态的标注' :
          project?.task_type==='vector'?'暂无图片描述或分类标注；检索匹配标注见上方审核区':<span>此项目还没有标注，{admin?<Link to={`/projects/${projectId}/images`}>前往项目图片开始标注</Link>:<ContinueAnnotation projectId={projectId} taskType={project?.task_type} reviewSearch={`project_id=${projectId}`}/>}</span> }}
        pagination={{ current: page, pageSize, total, showSizeChanger: true, pageSizeOptions: [20, 50, 100],
          showTotal: count => `共 ${count} 张已标注图片`, onChange: (page, page_size) => update({ page, page_size }) }} />
    </> : <>
      <p className="workspace-help">选择项目进行检测、分类、描述或向量匹配标注，项目间标注独立。{admin&&<Link to="/image-semantics/history">查看旧图片库生成批次</Link>}</p>
      <Card loading={loading} styles={{ body: { padding: 0 } }} variant="borderless">
        {!loading && !projects.length && <Empty description={error ? '项目读取失败，请重试' : '暂无标注项目'}
          image={Empty.PRESENTED_IMAGE_SIMPLE}>{!error && (admin?<Link to="/projects">前往项目管理创建项目</Link>:'请联系管理员创建项目并添加图片')}</Empty>}
        <Row gutter={[16, 16]}>
          {projects.map(item => <Col xs={24} md={12} xl={8} key={item.id}>
            <section aria-label={`标注项目 ${item.project_name}`}>
              <Card title={item.project_name}>
                <p className="review-project-description" title={item.description || ''}>{item.description || '未填写项目描述'}</p>
                {counts(item)}
                <Space wrap style={{ marginTop: 20 }}>
                  <ContinueAnnotation projectId={item.id} taskType={item.task_type} reviewSearch={`project_id=${item.id}`}/>
                  {admin&&<Link to={`/projects/${item.id}/prepare`}>切图 / 标注准备</Link>}
                  <Link to={`/annotations?project_id=${item.id}`}>审核标注</Link>
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
  </Card>;
}

